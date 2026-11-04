from flask import g, has_request_context, Flask, render_template, request, Response, jsonify, redirect, url_for, session, send_file
from io import BytesIO
from collections import deque
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties
import atexit
import importlib
import logging
from pathlib import Path
import secrets
import time
import datetime
import math
import os
import json
import socket
import threading
from werkzeug.security import generate_password_hash, check_password_hash
from database_layer import (
    connect as database_connect, ensure_schema, insert_measurement,
    DatabaseError, IntegrityError,
)

from camera_quality import CameraQuality
from pose_tasks import TasksPose, PoseLandmark
from types import SimpleNamespace
from ai_summary import SummaryJobs, summarize_facts

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = Path(os.getenv("ACL_MODEL_DIR", str(BASE_DIR.parent.parent / "models")))
POSE_MODEL = Path(os.getenv("ACL_POSE_MODEL", str(MODEL_DIR / "pose_landmarker_full.task")))
_summary_jobs = SummaryJobs(BASE_DIR / "litert_worker.py", Path(os.getenv("ACL_AI_ROOT", str(BASE_DIR.parent.parent))))


def _env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


def _env_int(name, default, minimum, maximum):
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


app = Flask(__name__, template_folder=str(BASE_DIR))
app.config.update(
    SECRET_KEY=os.getenv("ACL_SECRET_KEY") or secrets.token_hex(32),
    DATABASE=str(Path(os.getenv("ACL_DATABASE", str(BASE_DIR / "database.db"))).resolve()),
    DATABASE_URL=os.getenv("ACL_DATABASE_URL"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_env_bool("ACL_COOKIE_SECURE"),
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=8),
    HARDWARE_ENABLED=_env_bool("ACL_ENABLE_HARDWARE", True),
    CSRF_ENABLED=True,
)
logger = logging.getLogger(__name__)
_db_init_lock = threading.Lock()
_initialized_databases = set()


def connect_db():
    conn = database_connect(app.config.get("DATABASE_URL"), app.config["DATABASE"])
    if has_request_context():
        g.setdefault("_db_connections", []).append(conn)
    return conn


def _database_identity():
    return app.config.get("DATABASE_URL") or app.config["DATABASE"]


@app.teardown_request
def close_request_connections(error=None):
    try:
        for conn in g.pop("_db_connections", []):
            conn.close()
    finally:
        if g.pop("_hardware_guard", False):
            _state_lock.release()


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.context_processor
def security_context():
    return {"csrf_token": csrf_token}


@app.before_request
def protect_request():
    if request.endpoint in ("static", "health"):
        return None
    database = _database_identity()
    if database not in _initialized_databases:
        with _db_init_lock:
            if database not in _initialized_databases:
                init_db()
                _initialized_databases.add(database)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and app.config["CSRF_ENABLED"]:
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not isinstance(token, str) or not secrets.compare_digest(token, expected):
            return jsonify(success=False, message="Phiên làm việc hết hạn. Vui lòng tải lại trang."), 400
    if request.endpoint in _HARDWARE_ENDPOINTS | _PATIENT_ENDPOINTS:
        if "user_id" not in session:
            return jsonify(success=False, message="Vui lòng đăng nhập."), 401
        if session.get("role") != "patient":
            return jsonify(success=False, message="Chỉ tài khoản bệnh nhân được thực hiện thao tác này."), 403
    if request.endpoint in _HARDWARE_ENDPOINTS:
        if request.endpoint != "video_feed":
            # Keep ownership stable through reading/resetting/saving this request.
            _state_lock.acquire()
            g._hardware_guard = True
        with _state_lock:
            owns_device = _hardware_owner == session["user_id"] and _hardware_active()
            busy = _hardware_active() and not owns_device
            # Reading status must not reserve the device or expose another patient's data.
            if request.method == "GET" and request.endpoint != "video_feed" and not owns_device:
                return jsonify(active=False, busy=busy, angle=0, max_rom=0, tracking=False,
                               receiving=False, connected=False, age=None, jitter=0,
                               calibrating=False, use_imu=USE_IMU, mode=IMU_MODE, ports=[],
                               message="Bộ camera/IMU đang được sử dụng. Bạn vẫn có thể xem hồ sơ của mình." if busy else "Bấm Bắt đầu đo để kết nối camera/IMU.")
            if request.endpoint != "measurement_start" and not owns_device:
                return jsonify(success=False, code="device_busy" if busy else "measurement_inactive",
                               message="Bộ camera/IMU đang được người khác đo. Vui lòng chờ lượt." if busy else "Bấm Bắt đầu đo trước khi thao tác với thiết bị."), 409
            if not _claim_hardware(session["user_id"]):
                return jsonify(success=False, code="device_busy", message="Bộ camera/IMU đang được người khác đo. Hai tài khoản vẫn đăng nhập độc lập; vui lòng chờ thiết bị trống."), 409
        if USE_IMU and request.endpoint in _IMU_ENDPOINTS:
            _start_imu()


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response

# ===== CAU HINH =====
# Nguon camera cho MediaPipe:
#   0  = webcam laptop
#   1, 2 = webcam ao (vd iPhone qua Iriun/DroidCam)
#   "http://192.168.x.x:8081/video" = stream IP tu app camera tren iPhone
_camera_source = os.getenv("ACL_CAMERA_SOURCE", "Camo")
CAMERA_SOURCE = int(_camera_source) if _camera_source.isdecimal() else _camera_source
CAMERA_WIDTH = _env_int("ACL_CAMERA_WIDTH", 1920, 320, 3840)
CAMERA_HEIGHT = _env_int("ACL_CAMERA_HEIGHT", 1080, 240, 2160)
CAMERA_FPS = _env_int("ACL_CAMERA_FPS", 30, 5, 60)
_camera_details = {}
USE_IMU = _env_bool("ACL_USE_IMU", True)
IMU_MODE = os.getenv("ACL_IMU_MODE", "ble").lower()
if IMU_MODE not in ("ble", "wifi", "serial"):
    raise ValueError("ACL_IMU_MODE must be ble, wifi or serial")
IMU_HOST = os.getenv("ACL_IMU_HOST", "192.168.4.1")
IMU_TCP_PORT = _env_int("ACL_IMU_TCP_PORT", 8080, 1, 65535)
IMU_PORT = os.getenv("ACL_IMU_PORT", "COM6")
IMU_BAUD = _env_int("ACL_IMU_BAUD", 115200, 1200, 4000000)
EXPORT_DIR = os.getenv("ACL_EXPORT_DIR", str(BASE_DIR / "exports"))
CARE_UPLOAD_DIR = Path(os.getenv("ACL_CARE_UPLOAD_DIR", str(BASE_DIR / "care_uploads"))).resolve()
CARE_IMAGE_MAX_BYTES = 5 * 1024 * 1024

def _save_and_send(wb, filename):
    """Serialize once and optionally keep an atomic local export copy."""
    for sheet in wb:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                    cell.data_type = "s"
    bio = BytesIO()
    wb.save(bio)
    if EXPORT_DIR:
        temporary = None
        try:
            directory = Path(EXPORT_DIR)
            directory.mkdir(parents=True, exist_ok=True)
            temporary = directory / f".{secrets.token_hex(8)}.tmp"
            temporary.write_bytes(bio.getvalue())
            temporary.replace(directory / Path(filename).name)
        except OSError:
            logger.warning("Could not save local export", exc_info=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def init_db():
    conn = connect_db()
    try:
        ensure_schema(conn)
        admin_password = os.getenv("ACL_ADMIN_PASSWORD")
        admin_username = os.getenv("ACL_ADMIN_USERNAME", "admin").strip()
        if admin_password and not conn.execute(
            "SELECT 1 FROM doctors WHERE username=?", (admin_username,)
        ).fetchone():
            if len(admin_password) < 10:
                raise ValueError("ACL_ADMIN_PASSWORD must contain at least 10 characters")
            conn.execute(
                "INSERT INTO doctors(username, password, name) VALUES (?, ?, ?)",
                (admin_username, generate_password_hash(admin_password), "Bác sĩ Quản trị"),
            )
            conn.commit()
    finally:
        conn.close()


def _care_updates_for(conn, patient_id):
    return conn.execute(
        """
        SELECT care_updates.id, care_updates.image_name, care_updates.note,
               care_updates.pain_level, care_updates.limb_side,
               care_updates.created_at, care_updates.doctor_reply,
               care_updates.doctor_reply_at, doctors.name
        FROM care_updates
        LEFT JOIN doctors ON doctors.id = care_updates.doctor_id
        WHERE care_updates.patient_id=?
        ORDER BY care_updates.id DESC
        """,
        (patient_id,),
    ).fetchall()


def _care_image_extension(data):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None

camera = None
pose = None
cv2 = None
mp_pose = None
POSE_W = 640                                   # do rong ANH DUA VAO MediaPipe (lon hon -> ro khop hon)
VIS_TH = 0.3                                    # nguong visibility (thap -> it rot khop)
HOLD_S = 0.5                                    # giu landmark cu bao lau khi rot tam thoi (giay)
MAX_JUMP = 0.18                                 # dau goi duoc phep nhay toi da/frame (chuan hoa) - chong nhay nguoi/nhieu
RELOCK_S = 0.6                                  # mat dau goi qua lau -> cho phep KHOA LAI o vi tri moi
# Quay NGHIENG chi do 1 chan (chan ro nhat) -> dung 1 khoa "active"
_leg_persist = {"active": None}                 # luu landmark gan nhat de "giu" khi rot khop
_leg_lastpos = {"active": None}                 # vi tri dau goi gan nhat (x,y,t) de loc nhay theo chuyen dong
_leg_flexbuf = {"active": deque(maxlen=5)}      # bo dem goc de loc trung vi (diet gai nhon)
# NEO HONG: ngoi tua ghe -> hong gan nhu DUNG YEN nhung hay bi lung ghe che/danh lua.
# Giu vi tri hong on dinh (EMA cham); khi hong bi che/nhay xa -> dung lai hong da neo.
_hip_anchor = {"active": None}                  # vi tri hong da neo (hx, hy) chuan hoa
HIP_EMA = 0.12                                   # toc do cap nhat hong (thap = on dinh, vi hong it di chuyen)
HIP_JUMP = 0.12                                  # hong nhay xa hon nguong nay -> coi nhu bi ghe danh lua, GIU hong cu

# --- Goc do tu CAMERA ---
current_angle = 0
max_rom_session = 0
cam_leg = "auto"         # chan do bang camera: "left" | "right" | "auto" (chi de dat ten chan)
crowd_mode = False       # che do DONG NGUOI: chi xu ly nguoi trong VUNG GIUA (bo qua nguoi xung quanh)
ROI_L, ROI_R, ROI_T, ROI_B = 0.22, 0.78, 0.0, 1.0   # vung giua khi bat che do dong nguoi (~56% rong)

class OneEuroFilter:
    """One-Euro filter - chuan vang loc tin hieu chuyen dong nguoi:
       dung yen thi CUC MUOT (giat ~0), di chuyen thi BAM NHANH (it tre).
       Tot hon Kalman cho viec nay vi Kalman bi vot lo (overshoot) khi dung dot ngot."""
    def __init__(self, mincutoff=0.3, beta=0.02, dcutoff=1.0):
        self.mincutoff = mincutoff       # nho hon = muot hon luc dung yen
        self.beta = beta                 # lon hon = bam nhanh hon luc di chuyen (nhung giat hon)
        self.dcutoff = dcutoff
        self.x_prev = None; self.dx_prev = 0.0; self.t_prev = None
    def _alpha(self, cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)
    def update(self, x, t):
        if self.x_prev is None:
            self.x_prev = x; self.t_prev = t; return x
        dt = t - self.t_prev; self.t_prev = t
        if dt <= 0 or dt > 0.5: dt = 0.033
        dx = (x - self.x_prev) / dt
        a_d = self._alpha(self.dcutoff, dt)
        dx_hat = a_d * dx + (1.0 - a_d) * self.dx_prev
        self.dx_prev = dx_hat
        cutoff = self.mincutoff + self.beta * abs(dx_hat)   # cutoff thich nghi theo toc do
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1.0 - a) * self.x_prev
        self.x_prev = x_hat
        return x_hat

cam_filter_L = OneEuroFilter(mincutoff=0.15, beta=0.02)  # bo loc chan TRAI (mincutoff thap -> rat muot luc yen)
cam_filter_R = OneEuroFilter(mincutoff=0.15, beta=0.02)  # bo loc chan PHAI

# --- Goc do tu CAM BIEN IMU (rieng biet de chay song song + so sanh) ---
imu_angle = 0
imu_max_rom = 0
imu_jitter = 0.0                  # do dao dong (rung) cua tin hieu gan day
_imu_buf = deque(maxlen=15)       # bo dem goc gan nhat de tinh jitter
imu_calibrating = False           # dang lay gyro bias luc boot (giu yen)
imu_last_data = 0.0               # thoi diem nhan duoc dong du lieu gan nhat
current_port = IMU_PORT            # cong dang dung (USB hoac Bluetooth) - doi duoc luc chay
_want_reconnect = False            # co hieu: yeu cau ket noi lai cong khac
imu_serial = None
imu_connected = False

_state_lock = threading.RLock()
_worker_lock = threading.Lock()
_imu_write_lock = threading.Lock()
_frame_condition = threading.Condition()
_stop_event = threading.Event()
_camera_thread = None
_imu_thread = None
_latest_frame = None
_frame_number = 0
_camera_error = None
_stream_slots = threading.BoundedSemaphore(2)
_camera_quality = CameraQuality()
_camera_peak = None
camera_last_data = 0.0
_hardware_owner = None
_hardware_last_seen = 0.0
_hardware_generation = 0
HARDWARE_LEASE_SECONDS = 15.0
_HARDWARE_ENDPOINTS = {
    "combined_status", "save_combined", "measurement_start", "video_feed", "get_angle", "get_max_rom", "set_leg", "toggle_crowd",
    "get_imu_angle", "get_imu_max_rom", "imu_status", "imu_reconnect",
    "set_imu_mode", "imu_ports", "imu_connect", "imu_cmd", "reset_rom", "save_rom",
}
_IMU_ENDPOINTS = {"combined_status", "get_imu_angle", "get_imu_max_rom", "imu_status", "imu_reconnect", "set_imu_mode", "imu_connect", "imu_cmd"}
_PATIENT_ENDPOINTS = {
    "save_rom", "get_measurements", "delete_all_me",
    "care_upload", "measurement_stop",
}


def _reset_tracking():
    global current_angle, max_rom_session, imu_angle, imu_max_rom, camera_last_data, imu_last_data
    global cam_filter_L, cam_filter_R, _latest_frame, imu_jitter, _camera_peak
    current_angle = max_rom_session = imu_angle = imu_max_rom = 0
    camera_last_data = imu_last_data = 0.0
    imu_jitter = 0.0
    _imu_buf.clear()
    _leg_persist["active"] = _leg_lastpos["active"] = _hip_anchor["active"] = None
    _leg_flexbuf["active"].clear()
    cam_filter_L = OneEuroFilter(mincutoff=0.15, beta=0.02)
    cam_filter_R = OneEuroFilter(mincutoff=0.15, beta=0.02)
    _latest_frame = None
    _camera_peak = None
    _camera_quality.reset()
    if pose is not None and hasattr(pose, "reset"):
        pose.reset()


def _claim_hardware(patient_id):
    global _hardware_owner, _hardware_last_seen, _hardware_generation
    now = time.monotonic()
    with _state_lock:
        if _hardware_owner != patient_id or not _hardware_active():
            if _hardware_owner is not None and now - _hardware_last_seen < HARDWARE_LEASE_SECONDS:
                return False
            _reset_tracking()
            _hardware_owner = patient_id
            _hardware_generation += 1
        _hardware_last_seen = now
    return True


def _hardware_active():
    return _hardware_owner is not None and time.monotonic() - _hardware_last_seen < HARDWARE_LEASE_SECONDS


def _start_imu():
    global _imu_thread
    if not USE_IMU or not app.config["HARDWARE_ENABLED"]:
        return
    with _worker_lock:
        if _imu_thread is None or not _imu_thread.is_alive():
            _imu_thread = threading.Thread(target=imu_reader, name="acl-imu", daemon=True)
            _imu_thread.start()


def shutdown_resources():
    """Stop workers and release handles without opening hardware during import."""
    _stop_event.set()
    with _frame_condition:
        _frame_condition.notify_all()
    with _imu_write_lock:
        if imu_serial is not None:
            try:
                imu_serial.close()
            except OSError:
                pass
    for worker in (_camera_thread, _imu_thread):
        if worker and worker is not threading.current_thread():
            worker.join(timeout=6)


atexit.register(shutdown_resources)

def _process_imu_line(line):
    """Xu ly 1 dong tu ESP32: nhan biet boot/bias + tinh goc gap. Dung chung WiFi & Serial."""
    global imu_angle, imu_max_rom, imu_jitter, imu_calibrating, imu_last_data
    if not line:
        return
    if line.startswith("[LINK]"):
        logger.info("IMU radio: %s", line[:190])
        return
    if line.startswith(("[ERROR]", "[WAIT]", "[F]", "[RESET]", "[CALIB]")) or line == "... send C":
        with _state_lock:
            imu_calibrating = True
            imu_last_data = 0.0
            _imu_buf.clear()
            imu_jitter = 0
        logger.info("IMU calibration: %s", line[:190])
        return
    if line.startswith("[INFO]") or line.startswith("[WARN]"):
        low = line.lower()
        if "giu yen" in low or "rung" in low:
            imu_calibrating = True
        elif "san sang" in low:
            imu_calibrating = False
        return
    knee = None
    if line.startswith("Knee:"):                     # "Knee:83.4 [axis]"
        try: knee = float(line.split(":")[1].split()[0])
        except (ValueError, IndexError): pass
    elif "," in line:                                 # che do log "millis,knee"
        parts = line.split(",")
        if len(parts) == 2:
            try: knee = float(parts[1])
            except ValueError: pass
    if knee is not None and math.isfinite(knee) and 0 <= knee <= 200:
        with _state_lock:
            imu_last_data = time.monotonic()
            imu_calibrating = False
            imu_angle = round(180 - knee, 1)
            if _hardware_active():
                imu_max_rom = max(imu_max_rom, imu_angle)
            _imu_buf.append(imu_angle)
            if len(_imu_buf) >= 5:
                imu_jitter = max(_imu_buf) - min(_imu_buf)

def imu_reader():
    """Bounded reads and deterministic cleanup for BLE, serial and TCP."""
    global imu_serial, imu_connected, imu_calibrating, imu_last_data, _want_reconnect
    while not _stop_event.is_set():
        if not _hardware_active():
            _stop_event.wait(0.5)
            continue
        connection = None
        try:
            mode, opened = IMU_MODE, current_port
            _want_reconnect = False
            if mode == "ble":
                from ble_transport import BleStream
                connection = BleStream(timeout=0.2)
            elif mode == "wifi":
                connection = socket.create_connection((IMU_HOST, IMU_TCP_PORT), timeout=3)
                connection.settimeout(1)
            else:
                serial_module = importlib.import_module("serial")
                connection = serial_module.Serial(opened, IMU_BAUD, timeout=1, write_timeout=1)
            with _imu_write_lock:
                imu_serial = connection
                imu_connected = mode != "ble" or connection.connected
                imu_calibrating = True
            pending = b""
            discarding = False
            while not _stop_event.is_set() and _hardware_active():
                if _want_reconnect or mode != IMU_MODE or (mode == "serial" and opened != current_port):
                    break
                try:
                    raw = connection.recv(4096) if mode == "wifi" else connection.read(256)
                    if mode == "ble":
                        imu_connected = connection.connected
                except socket.timeout:
                    continue
                if not raw and mode == "wifi":
                    raise OSError("ESP32 disconnected")
                pending += raw
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if not discarding and len(line) <= 1024:
                        _process_imu_line(line.decode("utf-8", errors="ignore").strip())
                    discarding = False
                if len(pending) > 1024:
                    pending = b""
                    discarding = True
        except (OSError, ImportError, ValueError):
            logger.warning("IMU connection failed; retrying in 3 seconds", exc_info=True)
        finally:
            with _imu_write_lock:
                imu_connected = False
                imu_last_data = 0.0
                imu_serial = None
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
        _stop_event.wait(3)

def imu_send(cmd):
    """Gui lenh dieu khien (C/F/L/R) toi ESP32 (WiFi hoac Serial)."""
    global imu_serial, imu_connected
    with _imu_write_lock:
        if imu_serial and imu_connected:
            try:
                data = cmd.encode("ascii")
                if hasattr(imu_serial, "sendall"):
                    imu_serial.sendall(data)
                else:
                    imu_serial.write(data)
                return True
            except OSError:
                imu_connected = False
    return False

def calculate_angle(a, b, c):
    if math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-8 or math.hypot(c[0] - b[0], c[1] - b[1]) < 1e-8:
        raise ValueError("Landmarks overlap")
    radians = math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(a[1] - b[1], a[0] - b[0])
    angle = abs(math.degrees(radians))
    if angle > 180:
        angle = 360 - angle
    return angle

# --- ĐĂNG NHẬP / ĐĂNG KÝ ---

@app.route("/")
def home():
    if "user_id" in session:
        if session["role"] == "doctor":
            return redirect(url_for("admin"))
        return redirect(url_for("dashboard"))
    return render_template("home.html")

@app.route("/register", methods=["POST"])
def register():
    try:
        username = request.form["username"].strip()
        password = request.form["password"]
        name = request.form["name"].strip()
        age = int(request.form["age"])
        week = int(request.form["week"])
        if not 3 <= len(username) <= 64 or any(ch.isspace() for ch in username):
            raise ValueError("Tên đăng nhập phải có 3–64 ký tự, không chứa khoảng trắng.")
        if not 10 <= len(password) <= 256:
            raise ValueError("Mật khẩu phải có 10–256 ký tự.")
        if not 1 <= len(name) <= 120 or not 1 <= age <= 120 or not 0 <= week <= 104:
            raise ValueError("Họ tên, tuổi hoặc tuần phục hồi không hợp lệ.")
        
        hashed_password = generate_password_hash(password)
        
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO patients (username, password, name, age, week, doctor_advice)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (username, hashed_password, name, age, week, ""))
        conn.commit()
        msg, category = "Đăng ký thành công! Vui lòng đăng nhập.", "success"
    except IntegrityError:
        msg, category = "Tên đăng nhập đã tồn tại!", "danger"
    except (ValueError, KeyError):
        return render_template("home.html", msg="Thông tin không hợp lệ. Kiểm tra tên đăng nhập, mật khẩu (ít nhất 10 ký tự), tuổi và tuần phục hồi.", category="danger", active_portal="register"), 400
    except DatabaseError:
        logger.exception("Registration failed")
        return render_template("home.html", msg="Chưa thể tạo tài khoản. Vui lòng thử lại.", category="danger", active_portal="register"), 503
    finally:
        if 'conn' in locals(): conn.close()
        
    return render_template("home.html", msg=msg, category=category, active_portal="patient" if category == "success" else "register")

def _login_account(account_type):
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if account_type not in ("patient", "doctor"):
        return render_template("home.html", msg="Cổng đăng nhập không hợp lệ.", category="danger"), 400
    if not username or len(username) > 64 or not password or len(password) > 256:
        return render_template(
            "home.html", msg="Sai tài khoản hoặc mật khẩu!", category="danger",
            active_portal=account_type,
        ), 400

    table = "patients" if account_type == "patient" else "doctors"
    conn = connect_db()
    try:
        user = conn.execute(
            f"SELECT id, username, password, name FROM {table} WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()

    if not user or not check_password_hash(user[2], password):
        return render_template(
            "home.html", msg="Sai tài khoản hoặc mật khẩu!", category="danger",
            active_portal=account_type,
        ), 400

    session.clear()
    session.permanent = True
    session["user_id"] = user[0]
    session["username"] = user[1]
    session["role"] = account_type
    session["name"] = user[3]
    return redirect(url_for("admin" if account_type == "doctor" else "dashboard"))


@app.route("/login/patient", methods=["POST"])
def login_patient():
    return _login_account("patient")


@app.route("/login/doctor", methods=["POST"])
def login_doctor():
    return _login_account("doctor")


@app.route("/login", methods=["POST"])
def login_legacy():
    """Compatibility endpoint for old bookmarks and desktop builds."""
    return _login_account(request.form.get("account_type", "patient"))

@app.route("/logout", methods=["GET", "POST"])
def logout():
    global _hardware_owner, _hardware_last_seen
    with _state_lock:
        if session.get("role") == "patient" and _hardware_owner == session.get("user_id"):
            _hardware_owner = None
            _hardware_last_seen = 0.0
            _reset_tracking()
    session.clear()
    return redirect(url_for("home"))



@app.route("/dashboard")
def dashboard():
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("home"))
        
    conn = connect_db()
    cursor = conn.cursor()
   
    cursor.execute("SELECT age, week, doctor_advice FROM patients WHERE id = ?", (session["user_id"],))
    user_info = cursor.fetchone()
    
    
    cursor.execute("SELECT angle, time, source, id FROM measurements WHERE patient_id = ? ORDER BY id DESC", (session["user_id"],))
    history_rows = cursor.fetchall()
    conn.close()

    if user_info is None:
        session.clear()
        return redirect(url_for("home"))
    age, week, custom_advice = user_info
    week = week or 0

    if week <= 2:
        phase, target = "Giai đoạn 1", "0° - 90°"
        recommendation = {"muc_tieu": "Kiểm soát đau và sưng", "loi_khuyen": ["Chườm lạnh sau tập", "Co cơ tĩnh cơ tứ đầu", "Không gập quá 90°"]}
    elif week <= 6:
        phase, target = "Giai đoạn 2", "90° - 125°"
        recommendation = {"muc_tieu": "Tăng ROM và sức cơ", "loi_khuyen": ["Đạp xe tại chỗ", "Tập squat nông", "Tăng biên độ từ từ"]}
    else:
        phase, target = "Giai đoạn 3", "125° - 135°"
        recommendation = {"muc_tieu": "Khôi phục vận động toàn diện", "loi_khuyen": ["Đi bộ nhanh", "Chạy bộ nhẹ", "Tập thăng bằng chân nâng cao"]}

  
    if custom_advice:
        recommendation["loi_khuyen"].insert(0, f"⭐ LỜI KHUYÊN BÁC SĨ: {custom_advice}")

    return render_template("dashboard.html", name=session["name"], age=age, week=week, phase=phase, target=target, recommendation=recommendation, history=history_rows, use_imu=USE_IMU, imu_mode=IMU_MODE)

@app.route("/profile")
def profile():
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("home"))
        
    pid = session["user_id"]
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, age, week, username FROM patients WHERE id=?", (pid,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return "Không tìm thấy hồ sơ!", 404
        
    # Lay ROM lon nhat moi ngay, tach rieng Camera va Cam bien de ve 2 duong
    cursor.execute("""
        SELECT SUBSTR(time,1,10) AS d,
               MAX(CASE WHEN source='camera' THEN angle END),
               MAX(CASE WHEN source='imu'    THEN angle END)
        FROM measurements WHERE patient_id=? GROUP BY d ORDER BY d ASC
    """, (pid,))
    chart_data = cursor.fetchall()
    conn.close()
    dates = [row[0] if row[0] else "" for row in chart_data]
    cam_angles = [row[1] for row in chart_data]
    imu_angles = [row[2] for row in chart_data]

    return render_template("profile.html", user=user,
                           dates=json.dumps(dates, ensure_ascii=False),
                           cam_angles=json.dumps(cam_angles),
                           imu_angles=json.dumps(imu_angles))

@app.route("/my_records")
def my_records():
    """Trang ho so: benh nhan xem lai qua trinh tap theo tung ngay."""
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("home"))
    pid = session["user_id"]
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, age, week, doctor_advice FROM patients WHERE id=?", (pid,))
    info = cursor.fetchone()
    # Tong hop theo NGAY: so luot, ROM camera tot nhat, ROM cam bien tot nhat
    cursor.execute("""
        SELECT SUBSTR(time,1,10) AS d, COUNT(*),
               MAX(CASE WHEN source='camera' THEN angle END),
               MAX(CASE WHEN source='imu'    THEN angle END)
        FROM measurements WHERE patient_id=? GROUP BY d ORDER BY d DESC
    """, (pid,))
    days = cursor.fetchall()
    # Chi tiet tung luot do
    cursor.execute("SELECT time, angle, source FROM measurements WHERE patient_id=? ORDER BY id DESC", (pid,))
    allm = cursor.fetchall()
    conn.close()
    return render_template("my_records.html", info=info, days=days, allm=allm)


@app.route("/care")
def care_timeline():
    if "user_id" not in session or session.get("role") != "patient":
        return redirect(url_for("home"))
    patient_id = session["user_id"]
    conn = connect_db()
    patient = conn.execute(
        "SELECT id, name, age, week, doctor_advice FROM patients WHERE id=?",
        (patient_id,),
    ).fetchone()
    if patient is None:
        conn.close()
        session.clear()
        return redirect(url_for("home"))
    updates = _care_updates_for(conn, patient_id)
    measurement = conn.execute(
        "SELECT COUNT(*), MAX(angle) FROM measurements WHERE patient_id=?",
        (patient_id,),
    ).fetchone()
    conn.close()
    return render_template(
        "care_timeline.html",
        patient=patient,
        updates=updates,
        measurement_count=measurement[0] or 0,
        best_rom=measurement[1],
        care_message=request.args.get("care_message", ""),
        care_error=request.args.get("care_error", ""),
    )


@app.route("/care/upload", methods=["POST"])
def care_upload():
    patient_id = session["user_id"]
    note = request.form.get("note", "").strip()
    side = request.form.get("limb_side", "right").strip().lower()
    pain_raw = request.form.get("pain_level", "").strip()
    upload = request.files.get("image")

    if len(note) > 1000 or side not in {"left", "right", "both"}:
        return redirect(url_for("care_timeline", care_error="Thông tin cập nhật không hợp lệ."))
    try:
        pain_level = int(pain_raw)
    except (TypeError, ValueError):
        pain_level = None
    if pain_level is None or not 0 <= pain_level <= 10:
        return redirect(url_for("care_timeline", care_error="Mức đau phải từ 0 đến 10."))
    if upload is None or not upload.filename:
        return redirect(url_for("care_timeline", care_error="Vui lòng chọn ảnh chân cần theo dõi."))

    data = upload.read(CARE_IMAGE_MAX_BYTES + 1)
    if not data or len(data) > CARE_IMAGE_MAX_BYTES:
        return redirect(url_for("care_timeline", care_error="Ảnh phải nhỏ hơn 5 MB."))
    extension = _care_image_extension(data)
    if extension is None:
        return redirect(url_for("care_timeline", care_error="Chỉ nhận ảnh JPG, PNG hoặc WebP."))

    CARE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    image_name = f"{secrets.token_hex(24)}{extension}"
    destination = CARE_UPLOAD_DIR / image_name
    temporary = CARE_UPLOAD_DIR / f".{image_name}.tmp"
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
        conn = connect_db()
        try:
            conn.execute(
                """
                INSERT INTO care_updates(
                    patient_id, image_name, note, pain_level, limb_side, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    patient_id, image_name, note, pain_level, side,
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, *DatabaseError):
        destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        logger.exception("Could not save care update")
        return redirect(url_for("care_timeline", care_error="Không thể lưu ảnh lúc này."))

    return redirect(url_for("care_timeline", care_message="Đã gửi cập nhật cho bác sĩ."))


@app.route("/care/image/<int:update_id>")
def care_image(update_id):
    if "user_id" not in session:
        return redirect(url_for("home"))
    conn = connect_db()
    row = conn.execute(
        "SELECT patient_id, image_name FROM care_updates WHERE id=?",
        (update_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return "Không tìm thấy ảnh.", 404
    if session.get("role") != "doctor" and not (
        session.get("role") == "patient" and session.get("user_id") == row[0]
    ):
        return "Quyền truy cập bị từ chối.", 403
    image_path = CARE_UPLOAD_DIR / row[1]
    if not image_path.is_file():
        return "Tệp ảnh không còn tồn tại.", 404
    return send_file(image_path, conditional=True, max_age=0)



def _process_camera_frame(frame):
    global current_angle, max_rom_session, camera_last_data, _camera_peak
    global cam_filter_L, cam_filter_R
    h, w, _ = frame.shape
    rx0, rx1 = (int(ROI_L*w), int(ROI_R*w)) if crowd_mode else (0, w)
    ry0, ry1 = (int(ROI_T*h), int(ROI_B*h)) if crowd_mode else (0, h)
    rw, rh = max(1, rx1-rx0), max(1, ry1-ry0)
    small = cv2.resize(frame[ry0:ry1, rx0:rx1], (POSE_W, max(1, int(rh*POSE_W/rw))))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    rgb.flags.writeable = False
    result = pose.process(rgb)
    frame = cv2.flip(frame, 1)
    now = time.monotonic()
    points = None
    side = _camera_quality.side
    if result.pose_landmarks:
        lm = result.pose_landmarks.landmark
        P = mp_pose.PoseLandmark
        candidates = {
            'left': tuple(lm[p.value] for p in (P.LEFT_HIP, P.LEFT_KNEE, P.LEFT_ANKLE)),
            'right': tuple(lm[p.value] for p in (P.RIGHT_HIP, P.RIGHT_KNEE, P.RIGHT_ANKLE)),
        }
        side = cam_leg if cam_leg != 'auto' else side or max(candidates, key=lambda key: min(p.visibility for p in candidates[key]))
        points = candidates[side]
    was_ready = _camera_quality.status['ready']
    status = _camera_quality.check(points, now, small.shape[1], small.shape[0], brightness, sharpness)
    if status['code'] in ('settling', 'ready'):
        _camera_quality.side = side
    if status['ready']:
        _camera_quality.side = side
        if not was_ready:
            # Never mix a stale estimate from before occlusion with the new samples.
            _leg_flexbuf['active'].clear()
            cam_filter_L = OneEuroFilter(mincutoff=0.15, beta=0.02)
            cam_filter_R = OneEuroFilter(mincutoff=0.15, beta=0.02)
        coords = [((rx0+p.x*rw), (ry0+p.y*rh)) for p in points]
        flex = max(0, min(170, 180-calculate_angle(*coords)))
        buf = _leg_flexbuf['active']
        buf.append(flex)
        median = sorted(buf)[len(buf)//2]
        filt = cam_filter_L if side == 'left' else cam_filter_R
        current_angle = int(round(filt.update(median, now)))
        camera_last_data = now
        draw_points = [(int(w-x), int(y)) for x,y in coords]
        cv2.line(frame, draw_points[0], draw_points[1], (0,220,150), 5)
        cv2.line(frame, draw_points[1], draw_points[2], (0,220,150), 5)
        cv2.circle(frame, draw_points[1], 8, (0,0,255), -1)
    if crowd_mode:
        cv2.rectangle(frame, (w-rx1, ry0), (w-rx0, ry1), (255,180,0), 2)
    captured_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    label = f"Camera: {current_angle} deg | {side or 'auto'}" if status['ready'] else 'CHO HINH DU RO - KHONG GHI GOC'
    cv2.putText(frame, label, (16,30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,220,150) if status['ready'] else (0,180,255), 2)
    cv2.putText(frame, captured_at, (16,58), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 2)
    ok, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    jpeg = buffer.tobytes() if ok else None
    if jpeg and status['ready'] and (_camera_peak is None or current_angle > max_rom_session):
        max_rom_session = current_angle
        _camera_peak = {'jpeg': jpeg, 'angle': current_angle, 'captured_at': captured_at,
                        'side': side, 'owner': _hardware_owner, 'generation': _hardware_generation}
    return jpeg



def _open_camera():
    global cv2, mp_pose, camera, pose
    cv2 = importlib.import_module("cv2")
    mp_pose = SimpleNamespace(PoseLandmark=PoseLandmark)
    source, backend, name = CAMERA_SOURCE, cv2.CAP_ANY, str(CAMERA_SOURCE)
    if isinstance(source, str) and not source.startswith(("http://", "https://", "rtsp://")):
        enumerator = importlib.import_module("cv2_enumerate_cameras")
        candidates = list(enumerator.enumerate_cameras(cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY))
        matches = [item for item in candidates if item.name.casefold() == source.casefold()]
        if len(matches) != 1:
            raise OSError(f"Camera {source!r} not found uniquely. Open Camo Studio and its camera compatibility add-on.")
        selected = matches[0]
        source, backend, name = selected.index, selected.backend, selected.name
    camera = cv2.VideoCapture(source, backend)
    if not camera.isOpened():
        raise OSError("Camera unavailable")
    # Virtual cameras expose raw formats; forcing MJPG can break Camo's driver.
    if "camo" not in name.casefold():
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    camera.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
    _camera_details.update(name=name, width=int(camera.get(cv2.CAP_PROP_FRAME_WIDTH)),
                           height=int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                           fps=round(camera.get(cv2.CAP_PROP_FPS), 1))
    logger.info("Camera %s: %sx%s at %s FPS", name, _camera_details["width"],
                _camera_details["height"], _camera_details["fps"])
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    pose = TasksPose(POSE_MODEL)
    _camera_details.update(pose_api="Tasks API", pose_model="Full", pose_mode="VIDEO")


def _camera_worker():
    global _latest_frame, _frame_number, _camera_error, camera, pose
    try:
        _open_camera()
        failures = 0
        while not _stop_event.is_set() and _hardware_active():
            started = time.monotonic()
            with _state_lock:
                captured_generation = _hardware_generation
            success, frame = camera.read()
            if not success:
                failures += 1
                if failures >= 10:
                    raise OSError("Camera stopped returning frames")
                _stop_event.wait(0.1)
                continue
            failures = 0
            with _state_lock:
                if captured_generation != _hardware_generation or not _hardware_active():
                    continue
                jpeg = _process_camera_frame(frame)
                if jpeg is not None:
                    with _frame_condition:
                        _latest_frame = jpeg
                        _frame_number += 1
                        _camera_error = None
                        _frame_condition.notify_all()
            _stop_event.wait(max(0, 1 / 30 - (time.monotonic() - started)))
    except Exception:
        logger.exception("Camera worker stopped")
        _camera_error = "Không thể mở camera. Mở Camo Studio, ghép điện thoại và kiểm tra đã có hình trong Camo."
    finally:
        if camera is not None:
            camera.release()
            camera = None
        if pose is not None:
            pose.close()
            pose = None
        with _frame_condition:
            _frame_condition.notify_all()


def _start_camera():
    global _camera_thread, _camera_error
    if not app.config["HARDWARE_ENABLED"] or _stop_event.is_set():
        return False
    with _worker_lock:
        if _camera_thread is None or not _camera_thread.is_alive():
            _camera_error = None
            _camera_thread = threading.Thread(target=_camera_worker, name="acl-camera", daemon=True)
            _camera_thread.start()
    return True


def generate_frames(patient_id):
    """Fan out the latest JPEG: one capture/inference worker for all viewers."""
    previous = -1
    while not _stop_event.is_set():
        with _state_lock:
            if _hardware_owner != patient_id or not _hardware_active():
                return
        with _frame_condition:
            _frame_condition.wait_for(
                lambda: (_latest_frame is not None and _frame_number != previous) or _stop_event.is_set() or _camera_error,
                timeout=2,
            )
            if _stop_event.is_set() or _camera_error:
                return
            if _frame_number == previous or _latest_frame is None:
                continue
            previous, jpeg = _frame_number, _latest_frame
        with _state_lock:
            if _hardware_owner != patient_id:
                return
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"


@app.route("/measurement/start", methods=["POST"])
def measurement_start():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(success=False, message='Yêu cầu bắt đầu đo không hợp lệ.'), 400
    if payload.get("combined"):
        _reset_tracking()
    return jsonify(success=True, active=True)


@app.route("/measurement/stop", methods=["POST"])
def measurement_stop():
    global _hardware_owner, _hardware_last_seen
    with _state_lock:
        if _hardware_owner == session["user_id"]:
            _hardware_owner = None
            _hardware_last_seen = 0.0
            _reset_tracking()
    return jsonify(success=True, active=False)


@app.route("/health")
def health():
    return jsonify(status="ok", app="KneeROM", build="no-game-ble-20261104",
                   pose_api="tasks", pose_model_ready=POSE_MODEL.is_file(),
                   llm_runtime="LiteRT-LM", llm_ready=_summary_jobs.available())


@app.route('/video_feed')
def video_feed():
    if "user_id" not in session: return "Unauthorized", 401
    if not _start_camera():
        return jsonify(success=False, message="Camera đang bị tắt trong cấu hình."), 503
    with _frame_condition:
        _frame_condition.wait_for(lambda: _latest_frame is not None or _camera_error is not None, timeout=8)
    if _latest_frame is None:
        return jsonify(success=False, message=_camera_error or "Camera chưa sẵn sàng."), 503
    if not _stream_slots.acquire(blocking=False):
        return jsonify(success=False, message="Đang mở nhiều cửa sổ camera. Đóng bớt cửa sổ rồi thử lại."), 429
    frames = generate_frames(session["user_id"])
    released = False
    def release_slot():
        nonlocal released
        if not released:
            released = True
            _stream_slots.release()
    def bounded_frames():
        try:
            yield from frames
        finally:
            release_slot()
    response = Response(bounded_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-store, no-cache, max-age=0"
    response.call_on_close(release_slot)
    return response

@app.route("/get_angle")
def get_angle():
    _start_camera()
    with _state_lock:
        age = time.monotonic() - camera_last_data if camera_last_data else None
        return jsonify(angle=current_angle, tracking=_camera_quality.status['ready'] and age is not None and age < 1, age=age, quality=_camera_quality.status, snapshot_ready=_camera_peak is not None,
                       camera_available=_camera_error is None,
                       stream_ready=_latest_frame is not None and _camera_thread is not None and _camera_thread.is_alive(),
                       message=_camera_error, active=True, camera=_camera_details)

@app.route("/get_max_rom")
def get_max_rom():
    with _state_lock:
        return jsonify(max_rom=max_rom_session)


@app.route('/measurement/status')
def combined_status():
    _start_camera()
    with _state_lock:
        now = time.monotonic()
        camera_age = now-camera_last_data if camera_last_data else None
        imu_age = now-imu_last_data if imu_last_data else None
        ready = _camera_quality.status['ready'] and camera_age is not None and camera_age < 1
        receiving = USE_IMU and imu_connected and not imu_calibrating and imu_age is not None and imu_age < 3
        return jsonify(active=True, camera=dict(angle=current_angle, max_rom=max_rom_session,
            tracking=ready, age=camera_age, quality=_camera_quality.status, snapshot_ready=_camera_peak is not None,
            stream_ready=_latest_frame is not None and _camera_thread is not None and _camera_thread.is_alive(),
            message=_camera_error, camera=_camera_details), imu=dict(angle=imu_angle, max_rom=imu_max_rom,
            receiving=receiving, connected=imu_connected, age=imu_age, jitter=imu_jitter, calibrating=imu_calibrating))

@app.route("/set_leg/<leg>", methods=["POST"])
def set_leg(leg):
    """Chon chan do bang camera: left / right / auto."""
    global cam_leg
    if "user_id" not in session: return jsonify({"success": False}), 401
    if leg in ("left", "right", "auto"):
        with _state_lock:
            cam_leg = leg
            _reset_tracking()
            _leg_persist["active"] = _leg_lastpos["active"] = _hip_anchor["active"] = None
            _leg_flexbuf["active"].clear()
        return jsonify({"success": True, "leg": leg})
    return jsonify({"success": False, "message": "Chan khong hop le"}), 400


@app.route("/toggle_crowd", methods=["POST"])
def toggle_crowd():
    """Bat/tat che do dong nguoi (chi do nguoi dung trong vung giua)."""
    global crowd_mode
    if "user_id" not in session: return jsonify({"success": False}), 401
    with _state_lock:
        crowd_mode = not crowd_mode
        _reset_tracking()
        _leg_persist["active"] = _leg_lastpos["active"] = _hip_anchor["active"] = None
        _leg_flexbuf["active"].clear()
    return jsonify({"success": True, "crowd": crowd_mode})

@app.route("/get_imu_angle")
def get_imu_angle():
    with _state_lock:
        age = time.monotonic() - imu_last_data if imu_last_data else None
        return jsonify(angle=imu_angle, receiving=imu_connected and age is not None and age < 3, age=age)

@app.route("/get_imu_max_rom")
def get_imu_max_rom():
    with _state_lock:
        return jsonify(max_rom=imu_max_rom)

@app.route("/imu_status")
def imu_status():
    receiving = imu_connected and imu_last_data > 0 and (time.monotonic() - imu_last_data < 3.0)
    where = f"{IMU_HOST}:{IMU_TCP_PORT}" if IMU_MODE == "wifi" else (os.getenv("ACL_BLE_NAME", "KneeROM_7C0A7C") if IMU_MODE == "ble" else current_port)
    return jsonify({"connected": imu_connected, "receiving": receiving, "use_imu": USE_IMU,
                    "jitter": round(imu_jitter, 1), "calibrating": imu_calibrating,
                    "mode": IMU_MODE, "where": where, "port": current_port})

@app.route("/imu_reconnect", methods=["POST"])
def imu_reconnect():
    """Buoc ket noi lai ESP32 (dung cho ca WiFi va Serial)."""
    global _want_reconnect
    if "user_id" not in session: return jsonify({"success": False}), 401
    _want_reconnect = True
    return jsonify({"success": True})

@app.route("/set_imu_mode/<mode>", methods=["POST"])
def set_imu_mode(mode):
    """Chuyen kieu ket noi IMU luc dang chay: 'serial' (USB) hoac 'wifi'."""
    global IMU_MODE, _want_reconnect
    if "user_id" not in session: return jsonify({"success": False}), 401
    if mode not in ("ble", "serial", "wifi"):
        return jsonify({"success": False, "message": "Kieu khong hop le"})
    IMU_MODE = mode
    _want_reconnect = True               # ngat ket noi cu -> luong tu noi lai theo mode moi
    return jsonify({"success": True, "mode": mode})

@app.route("/imu_ports")
def imu_ports():
    """Liet ke cac cong COM (USB + Bluetooth) de nguoi dung chon."""
    from serial.tools import list_ports
    out = []
    for p in list_ports.comports():
        desc = p.description or ""
        out.append({"device": p.device, "desc": desc, "bluetooth": "bluetooth" in desc.lower()})
    return jsonify({"ports": out, "current": current_port})

@app.route("/imu_connect/<port>", methods=["POST"])
def imu_connect(port):
    """Doi cong ket noi (USB <-> Bluetooth) ngay luc dang chay."""
    global current_port, _want_reconnect
    if "user_id" not in session: return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    current_port = port
    _want_reconnect = True
    return jsonify({"success": True, "port": port})

@app.route("/imu_cmd/<cmd>", methods=["POST"])
def imu_cmd(cmd):
    if "user_id" not in session: return jsonify({"success": False, "message": "Chua dang nhap"}), 401
    cmd = cmd.upper()
    if cmd not in ("C", "F", "L", "R"):
        return jsonify({"success": False, "message": "Lenh khong hop le"})
    ok = imu_send(cmd)
    if ok and cmd in ('C', 'F', 'R'):
        _reset_tracking()
    return jsonify({"success": ok, "message": ("Da gui lenh " + cmd) if ok else "Cam bien IMU chua ket noi"})

@app.route("/get_measurements")
def get_measurements():
    if "user_id" not in session: return jsonify([])
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT angle, time FROM measurements WHERE patient_id = ? ORDER BY id DESC", (session["user_id"],))
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"angle": r[0], "time": r[1]} for r in rows])

@app.route("/reset_rom/<source>", methods=["POST"])
def reset_rom(source):
    global max_rom_session, imu_max_rom, camera_last_data, imu_last_data, _camera_peak
    with _state_lock:
        if source == "camera":
            _camera_peak = None
            _camera_quality.reset()
            max_rom_session = 0
            camera_last_data = 0.0
        elif source == "imu":
            imu_max_rom = 0
            imu_last_data = 0.0
        else:
            return jsonify(success=False, message="Nguồn không hợp lệ."), 400
    return jsonify(success=True)



def _save_camera_capture(conn, measurement_id, measured_at, imu_value=None, imu_id=None):
    """Write one peak JPEG; caller commits image metadata and measurements together."""
    peak = _camera_peak
    if not peak or peak['owner'] != session['user_id'] or peak['generation'] != _hardware_generation:
        raise ValueError('Chưa có ảnh hợp lệ của lượt đo này.')
    CARE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(24) + '.jpg'
    destination = CARE_UPLOAD_DIR / name
    temporary = destination.with_suffix('.tmp')
    try:
        temporary.write_bytes(peak['jpeg'])
        temporary.replace(destination)
        note = (f"Ảnh tự động tại ROM camera lớn nhất: {peak['angle']}°. "
                f"Chụp lúc {peak['captured_at']}. Lượt camera #{measurement_id}.")
        if imu_value is not None:
            note += f" ROM IMU lớn nhất trong cùng lượt đo: {imu_value}° (#{imu_id}). Hai giá trị cực đại có thể ở hai thời điểm khác nhau."
        conn.execute('INSERT INTO care_updates(patient_id,image_name,note,pain_level,limb_side,created_at) VALUES(?,?,?,?,?,?)',
                     (session['user_id'], name, note, None, peak['side'], measured_at))
        update_id = conn.execute('SELECT id FROM care_updates WHERE patient_id=? AND image_name=?',
                                 (session['user_id'], name)).fetchone()[0]
        return destination, update_id
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


@app.route('/measurement/save_combined', methods=['POST'])
def save_combined():
    global max_rom_session, imu_max_rom, camera_last_data, imu_last_data, _camera_peak
    with _state_lock:
        now = time.monotonic()
        if not _camera_quality.status['ready'] or not camera_last_data or now-camera_last_data > 1 or not _camera_peak:
            return jsonify(success=False, message='Camera chưa có hình đủ rõ. Đưa đủ hông–gối–cổ chân vào khung.'), 409
        if not USE_IMU or not imu_connected or imu_calibrating or not imu_last_data or now-imu_last_data > 3:
            return jsonify(success=False, message='Chưa có tín hiệu IMU mới. Kiểm tra cảm biến trước khi lưu cả hai kết quả.'), 409
        values = {'camera': max_rom_session, 'imu': imu_max_rom}
        measured_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = connect_db()
        image_path = None
        try:
            ids = {source: insert_measurement(conn, session['user_id'], value, measured_at, source)
                   for source, value in values.items()}
            image_path, update_id = _save_camera_capture(conn, ids['camera'], measured_at, values['imu'], ids['imu'])
            conn.commit()
        except (*DatabaseError, OSError, ValueError):
            conn.rollback()
            if image_path is not None:
                image_path.unlink(missing_ok=True)
            logger.exception('Could not save paired measurement')
            return jsonify(success=False, message='Chưa lưu được lượt đo. Kết quả vẫn được giữ để bạn thử lại.'), 503
        finally:
            conn.close()
        captured_at = _camera_peak['captured_at']
        _camera_peak = None
        max_rom_session = imu_max_rom = 0
        camera_last_data = imu_last_data = 0.0
        return jsonify(success=True, camera_rom=values['camera'], imu_rom=values['imu'],
                       camera_id=ids['camera'], imu_id=ids['imu'], time=measured_at,
                       snapshot_url=url_for('care_image', update_id=update_id), captured_at=captured_at)



@app.route("/save_rom/<source>", methods=["POST"])
def save_rom(source):
    global max_rom_session, imu_max_rom, camera_last_data, imu_last_data
    if source not in ("camera", "imu"):
        return jsonify(success=False, message="Nguồn không hợp lệ."), 400
    with _state_lock:
        sampled_at = camera_last_data if source == "camera" else imu_last_data
        if not sampled_at or time.monotonic() - sampled_at > 3 or (source == "imu" and not imu_connected):
            return jsonify(success=False, message="Chưa có tín hiệu đo mới từ thiết bị."), 409
        value = max_rom_session if source == "camera" else imu_max_rom
        conn = connect_db()
        try:
            full_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_id = insert_measurement(
                conn, session["user_id"], value, full_time_str, source
            )
            conn.commit()
        finally:
            conn.close()
        if source == "camera":
            max_rom_session = 0
            camera_last_data = 0.0
        else:
            imu_max_rom = 0
            imu_last_data = 0.0
    return jsonify(success=True, saved_rom=value, source=source, time=full_time_str, id=new_id)


@app.route("/delete_measurement/<int:mid>", methods=["POST"])
def delete_measurement(mid):
    """Xoa 1 luot do sai. Bac si xoa bat ky; benh nhan chi xoa cua minh."""
    if "user_id" not in session: return jsonify({"success": False, "message": "Chưa đăng nhập"}), 401
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT patient_id FROM measurements WHERE id = ?", (mid,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False, "message": "Không tìm thấy lượt đo"})
    if session["role"] != "doctor" and session["user_id"] != row[0]:
        conn.close()
        return jsonify({"success": False, "message": "Không có quyền xóa"}), 403
    cursor.execute("DELETE FROM measurements WHERE id = ?", (mid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/export/<int:pid>")
def export_excel(pid):
    """Xuat lich su do cua 1 benh nhan ra Excel. Bac si bat ky; benh nhan chi cua minh."""
    if "user_id" not in session: return redirect(url_for("home"))
    if session["role"] != "doctor" and session["user_id"] != pid:
        return "Không có quyền truy cập", 403
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, age, week FROM patients WHERE id = ?", (pid,))
    u = cursor.fetchone()
    if not u:
        conn.close()
        return "Không tìm thấy bệnh nhân", 404
    name, age, week = u
    cursor.execute("SELECT angle, time, source FROM measurements WHERE patient_id = ? ORDER BY id ASC", (pid,))
    rows = cursor.fetchall()
    conn.close()

    wb = Workbook(); ws = wb.active; ws.title = "Lich su do"
    hdr_fill = PatternFill("solid", fgColor="2F5597")
    hdr_font = Font(bold=True, color="FFFFFF")
    ws.append([f"BỆNH NHÂN: {name}  |  Tuổi: {age}  |  Tuần phục hồi: {week}"])
    ws.append(["STT", "Thời gian", "Góc ROM (độ)", "Nguồn đo"])
    for i, (ang, t, src) in enumerate(rows, 1):
        ws.append([i, t, ang, "Cảm biến IMU" if src == "imu" else "Camera"])
    for c in range(1, 5):
        cell = ws.cell(2, c); cell.font = hdr_font; cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center")
    for col, w in zip("ABCD", [6, 22, 14, 16]): ws.column_dimensions[col].width = w

    safe = "".join(ch for ch in name if ch.isalnum() or ch in " _-").strip().replace(" ", "_")
    return _save_and_send(wb, f"lichsu_{safe or pid}.xlsx")

@app.route("/export_me")
def export_me():
    """Benh nhan tu xuat lich su cua chinh minh."""
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("home"))
    return export_excel(session["user_id"])

@app.route("/export_all")
def export_all():
    """Bac si xuat toan bo lich su cua moi benh nhan vao 1 file."""
    if "user_id" not in session or session["role"] != "doctor":
        return redirect(url_for("home"))
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT patients.name, measurements.angle, measurements.time, measurements.source
        FROM measurements LEFT JOIN patients ON patients.id = measurements.patient_id
        ORDER BY patients.name, measurements.id
    """)
    rows = cursor.fetchall()
    conn.close()

    wb = Workbook(); ws = wb.active; ws.title = "Tat ca benh nhan"
    hdr_fill = PatternFill("solid", fgColor="2F5597")
    hdr_font = Font(bold=True, color="FFFFFF")
    ws.append(["Bệnh nhân", "Góc ROM (độ)", "Thời gian", "Nguồn đo"])
    for c in range(1, 5):
        cell = ws.cell(1, c); cell.font = hdr_font; cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center")
    for name, ang, t, src in rows:
        ws.append([name, ang, t, "Cảm biến IMU" if src == "imu" else "Camera"])
    for col, w in zip("ABCD", [22, 14, 22, 16]): ws.column_dimensions[col].width = w

    return _save_and_send(wb, "lichsu_tat_ca_benh_nhan.xlsx")

@app.route("/delete_all_me", methods=["POST"])
def delete_all_me():
    """Benh nhan xoa TAT CA luot do cua chinh minh."""
    if "user_id" not in session or session["role"] != "patient":
        return jsonify({"success": False, "message": "Khong co quyen"}), 401
    conn = connect_db(); cur = conn.cursor()
    cur.execute("DELETE FROM measurements WHERE patient_id = ?", (session["user_id"],))
    conn.commit(); conn.close()
    return jsonify({"success": True})

@app.route("/delete_all/<int:pid>", methods=["POST"])
def delete_all(pid):
    """Xoa TAT CA luot do cua 1 benh nhan. Bac si bat ky; benh nhan chi cua minh."""
    if "user_id" not in session: return jsonify({"success": False}), 401
    if session["role"] != "doctor" and session["user_id"] != pid:
        return jsonify({"success": False, "message": "Khong co quyen"}), 403
    conn = connect_db(); cur = conn.cursor()
    cur.execute("DELETE FROM measurements WHERE patient_id = ?", (pid,))
    conn.commit(); conn.close()
    return jsonify({"success": True})

@app.route("/export_compare/<int:pid>")
def export_compare(pid):
    """Xuat Excel SO SANH Camera vs Cam bien (2 duong tren 1 bieu do)."""
    if "user_id" not in session: return redirect(url_for("home"))
    if session["role"] != "doctor" and session["user_id"] != pid:
        return "Không có quyền truy cập", 403
    conn = connect_db(); cur = conn.cursor()
    cur.execute("SELECT name FROM patients WHERE id = ?", (pid,))
    u = cur.fetchone()
    if not u:
        conn.close(); return "Không tìm thấy bệnh nhân", 404
    name = u[0]
    cur.execute("SELECT angle, time FROM measurements WHERE patient_id=? AND source='camera' ORDER BY id", (pid,))
    cams = cur.fetchall()
    cur.execute("SELECT angle, time FROM measurements WHERE patient_id=? AND source='imu' ORDER BY id", (pid,))
    imus = cur.fetchall()
    conn.close()

    wb = Workbook(); ws = wb.active; ws.title = "So sanh"
    hdr_fill = PatternFill("solid", fgColor="0E82FD"); hdr_font = Font(bold=True, color="FFFFFF")
    ws.append([f"SO SÁNH Camera vs Cảm biến — Bệnh nhân: {name}"])
    ws.append(["Lượt đo", "Thời gian", "Camera (độ)", "Cảm biến IMU (độ)", "Chênh lệch (độ)"])
    n = max(len(cams), len(imus))
    for i in range(n):
        cval = cams[i][0] if i < len(cams) else None
        ival = imus[i][0] if i < len(imus) else None
        tval = cams[i][1] if i < len(cams) else (imus[i][1] if i < len(imus) else "")
        diff = abs(cval - ival) if (cval is not None and ival is not None) else None
        ws.append([i + 1, tval, cval, ival, diff])
    last = n + 2
    for col_i in range(1, 6):
        cell = ws.cell(2, col_i); cell.font = hdr_font; cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center")
    for col_l, wdt in zip("ABCDE", [9, 20, 14, 16, 14]): ws.column_dimensions[col_l].width = wdt

    if n > 0:
        chart = LineChart(); chart.title = "Camera vs Cảm biến (góc ROM)"
        chart.y_axis.title = "Góc (độ)"; chart.x_axis.title = "Lượt đo"
        chart.height = 10; chart.width = 22
        chart.add_data(Reference(ws, min_col=3, max_col=4, min_row=2, max_row=last), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=3, max_row=last))
        # Hien ro CA 2 TRUC + thang doc len 180 do
        chart.x_axis.delete = False
        chart.y_axis.delete = False
        chart.y_axis.scaling.min = 0
        chart.y_axis.scaling.max = 180
        chart.y_axis.majorUnit = 30
        chart.x_axis.majorTickMark = "out"
        chart.y_axis.majorTickMark = "out"
        if len(chart.series) >= 1:
            s0 = chart.series[0]; s0.smooth = True
            s0.graphicalProperties = GraphicalProperties(); s0.graphicalProperties.line = LineProperties(solidFill="0E82FD", w=26000)
        if len(chart.series) >= 2:
            s1 = chart.series[1]; s1.smooth = True
            s1.graphicalProperties = GraphicalProperties(); s1.graphicalProperties.line = LineProperties(solidFill="16A34A", w=26000)
        ws.add_chart(chart, "G2")

    # ===== ĐÁNH GIÁ ĐỒNG THUẬN 2 PHƯƠNG PHÁP (Camera vs Cảm biến) =====
    paired = [(cams[i][0], imus[i][0]) for i in range(min(len(cams), len(imus)))
              if cams[i][0] is not None and imus[i][0] is not None]
    if paired:
        diffs = [c - m for c, m in paired]          # sai so co dau (camera - cam bien)
        ad = [abs(d) for d in diffs]
        nP = len(paired)
        mae = sum(ad) / nP                           # sai so tuyet doi trung binh
        rmse = (sum(d * d for d in diffs) / nP) ** 0.5
        bias = sum(diffs) / nP                        # do lech he thong (>0: camera doc cao hon)
        mx = max(ad)
        cam_v = [p[0] for p in paired]; imu_v = [p[1] for p in paired]
        mc = sum(cam_v) / nP; mi = sum(imu_v) / nP
        num = sum((cam_v[k] - mc) * (imu_v[k] - mi) for k in range(nP))
        den = (sum((x - mc) ** 2 for x in cam_v) * sum((x - mi) ** 2 for x in imu_v)) ** 0.5
        corr = (num / den) if den > 0 else 0.0
        if mae <= 5:    verdict = "Đồng thuận TỐT (sai số TB ≤ 5°) — 2 phương pháp tin cậy như nhau"
        elif mae <= 10: verdict = "Đồng thuận KHÁ (sai số 5–10°)"
        else:           verdict = "Chênh lệch LỚN (>10°) — nên kiểm tra lại đặt cảm biến/khung camera"
        who = "Camera đo cao hơn" if bias > 1 else ("Cảm biến đo cao hơn" if bias < -1 else "Gần như cân bằng")

        r0 = last + 2
        title_cell = ws.cell(r0, 1, "ĐÁNH GIÁ ĐỒNG THUẬN 2 PHƯƠNG PHÁP")
        title_cell.font = Font(bold=True, color="FFFFFF"); title_cell.fill = PatternFill("solid", fgColor="0E82FD")
        ws.cell(r0, 2).fill = PatternFill("solid", fgColor="0E82FD")
        rows_stat = [
            ("Số buổi so sánh", nP),
            ("Sai số tuyệt đối trung bình - MAE (độ)", round(mae, 1)),
            ("Sai số quân phương - RMSE (độ)", round(rmse, 1)),
            ("Độ lệch hệ thống - bias (độ)", f"{round(bias, 1)}  ({who})"),
            ("Sai số lớn nhất (độ)", round(mx, 1)),
            ("Hệ số tương quan (1 = trùng khớp)", round(corr, 3)),
            ("Kết luận", verdict),
        ]
        for k, (lab, val) in enumerate(rows_stat):
            ws.cell(r0 + 1 + k, 1, lab).font = Font(bold=True)
            ws.cell(r0 + 1 + k, 2, val)

    safe = "".join(ch for ch in name if ch.isalnum() or ch in " _-").strip().replace(" ", "_")
    return _save_and_send(wb, f"sosanh_{safe or pid}.xlsx")

@app.route("/export_compare_me")
def export_compare_me():
    """Benh nhan tu xuat Excel so sanh cua chinh minh."""
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("home"))
    return export_compare(session["user_id"])



@app.route("/admin")
def admin():
    if "user_id" not in session or session["role"] != "doctor":
        return redirect(url_for("home"))

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM patients")
    total_patients = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM measurements")
    total_measurements = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM care_updates")
    total_care_updates = cursor.fetchone()[0]
    cursor.execute("SELECT id, name, age, week, username FROM patients")
    patients = cursor.fetchall()
    cursor.execute("SELECT id, username, name FROM doctors ORDER BY id")
    doctors = cursor.fetchall()
    cursor.execute("""
        SELECT measurements.id, patients.name, measurements.angle, measurements.time
        FROM measurements
        LEFT JOIN patients ON patients.id = measurements.patient_id
        ORDER BY measurements.id DESC
    """)
    measurements = cursor.fetchall()
    conn.close()

    return render_template("admin_dashboard.html", total_patients=total_patients,
                           total_measurements=total_measurements,
                           total_care_updates=total_care_updates, patients=patients,
                           measurements=measurements, doctors=doctors,
                           can_manage_doctors=True,
                           doctor_message=request.args.get("doctor_message", ""),
                           doctor_error=request.args.get("doctor_error", ""))


def _doctor_admin_required():
    admin_username = os.getenv("ACL_ADMIN_USERNAME", "admin").strip().lower()
    username = session.get("username", "").strip().lower()
    return ("user_id" in session and session.get("role") == "doctor" and
            username in {"admin", admin_username})


def _valid_doctor_username(value):
    return 3 <= len(value) <= 64 and all(ch.isalnum() or ch in "._-" for ch in value)


@app.route("/admin/doctors/create", methods=["POST"])
def admin_doctor_create():
    if not _doctor_admin_required():
        return redirect(url_for("home"))
    username = request.form.get("username", "").strip().lower()
    name = request.form.get("name", "").strip()
    password = request.form.get("password", "")
    if not _valid_doctor_username(username) or not name or len(name) > 120 or len(password) < 10:
        return redirect(url_for("admin", doctor_error="Thông tin không hợp lệ; mật khẩu cần ít nhất 10 ký tự."))
    conn = connect_db()
    try:
        conn.execute("INSERT INTO doctors(username,password,name) VALUES(?,?,?)",
                     (username, generate_password_hash(password), name))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        return redirect(url_for("admin", doctor_error="Tên đăng nhập bác sĩ đã tồn tại."))
    finally:
        conn.close()
    return redirect(url_for("admin", doctor_message="Đã tạo tài khoản bác sĩ."))


@app.route("/admin/doctors/<int:doctor_id>/password", methods=["POST"])
def admin_doctor_password(doctor_id):
    if not _doctor_admin_required():
        return redirect(url_for("home"))
    password = request.form.get("password", "")
    if len(password) < 10:
        return redirect(url_for("admin", doctor_error="Mật khẩu cần ít nhất 10 ký tự."))
    conn = connect_db()
    cursor = conn.execute("UPDATE doctors SET password=? WHERE id=?",
                          (generate_password_hash(password), doctor_id))
    conn.commit()
    conn.close()
    message = "Đã đổi mật khẩu bác sĩ." if cursor.rowcount else "Không tìm thấy bác sĩ."
    return redirect(url_for("admin", doctor_message=message))


@app.route("/admin/doctors/<int:doctor_id>/delete", methods=["POST"])
def admin_doctor_delete(doctor_id):
    if not _doctor_admin_required():
        return redirect(url_for("home"))
    if doctor_id == session.get("user_id"):
        return redirect(url_for("admin", doctor_error="Không thể xóa tài khoản đang đăng nhập."))
    conn = connect_db()
    cursor = conn.execute("DELETE FROM doctors WHERE id=?", (doctor_id,))
    conn.commit()
    conn.close()
    message = "Đã xóa tài khoản bác sĩ." if cursor.rowcount else "Không tìm thấy bác sĩ."
    return redirect(url_for("admin", doctor_message=message))

@app.route("/admin/patient/<int:pid>")
def admin_patient_view(pid):
    if "user_id" not in session or session["role"] != "doctor":
        return redirect(url_for("home"))
        
    conn = connect_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, name, age, week, username, doctor_advice FROM patients WHERE id=?", (pid,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return "Không tìm thấy thông tin bệnh nhân này!", 404
        
    cursor.execute("SELECT angle, time, source, id FROM measurements WHERE patient_id=? ORDER BY id DESC", (pid,))
    history = cursor.fetchall()
    updates = _care_updates_for(conn, pid)
    conn.close()

    return render_template(
        "admin_patients_view.html",
        user=user,
        history=history,
        updates=updates,
        care_message=request.args.get("care_message", ""),
        care_error=request.args.get("care_error", ""),
    )


@app.route('/admin/patient/<int:pid>/ai-summary', methods=['POST'])
def doctor_ai_summary(pid):
    if 'user_id' not in session:
        return jsonify(success=False, message='Vui lòng đăng nhập bác sĩ.'), 401
    if session.get('role') != 'doctor':
        return jsonify(success=False, message='Chỉ bác sĩ được tạo bản tóm tắt.'), 403
    conn = connect_db()
    if conn.execute('SELECT 1 FROM patients WHERE id=?', (pid,)).fetchone() is None:
        return jsonify(success=False, message='Không tìm thấy bệnh nhân.'), 404
    rows = conn.execute('SELECT angle,time,source FROM measurements WHERE patient_id=? ORDER BY id DESC LIMIT 100', (pid,)).fetchall()
    if not rows:
        return jsonify(success=False, message='Chưa có dữ liệu đo để tóm tắt.'), 409
    try:
        job_id = _summary_jobs.start(session['user_id'], pid, summarize_facts(rows))
    except FileNotFoundError:
        return jsonify(success=False, message='LiteRT-LM chưa sẵn sàng trên máy chủ.'), 503
    if job_id is None:
        return jsonify(success=False, message='AI đang xử lý một bản tóm tắt. Thử lại sau ít phút.'), 429
    return jsonify(success=True, job_id=job_id, state='running'), 202


@app.route('/admin/patient/<int:pid>/ai-summary/<job_id>')
def doctor_ai_summary_status(pid, job_id):
    if 'user_id' not in session:
        return jsonify(success=False, message='Vui lòng đăng nhập bác sĩ.'), 401
    if session.get('role') != 'doctor':
        return jsonify(success=False, message='Chỉ bác sĩ được xem bản tóm tắt.'), 403
    job = _summary_jobs.get(job_id, session['user_id'], pid)
    if job is None:
        return jsonify(success=False, message='Không tìm thấy bản tóm tắt của phiên này.'), 404
    return jsonify(success=True, **job)


@app.route("/admin/care/<int:update_id>/reply", methods=["POST"])
def admin_care_reply(update_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return jsonify(success=False, message="Quyền truy cập bị từ chối"), 403
    reply = request.form.get("doctor_reply", "").strip()
    conn = connect_db()
    row = conn.execute(
        "SELECT patient_id FROM care_updates WHERE id=?",
        (update_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return "Không tìm thấy cập nhật.", 404
    if not reply or len(reply) > 2000:
        conn.close()
        return redirect(url_for(
            "admin_patient_view",
            pid=row[0],
            care_error="Phản hồi phải có từ 1 đến 2000 ký tự.",
        ))
    conn.execute(
        """
        UPDATE care_updates
        SET doctor_reply=?, doctor_reply_at=?, doctor_id=?
        WHERE id=?
        """,
        (
            reply,
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            session["user_id"],
            update_id,
        ),
    )
    conn.commit()
    conn.close()
    return redirect(url_for(
        "admin_patient_view",
        pid=row[0],
        care_message="Đã gửi phản hồi tới bệnh nhân.",
    ))


@app.route("/admin/save_advice/<int:pid>", methods=["POST"])
def admin_save_advice(pid):
    if "user_id" not in session or session["role"] != "doctor":
        return jsonify({"success": False, "message": "Quyền truy cập bị từ chối"}), 403
    
    advice_text = request.form.get("doctor_advice", "").strip()
    if len(advice_text) > 5000:
        return jsonify(success=False, message="Lời khuyên quá dài (tối đa 5000 ký tự)."), 400
    
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE patients SET doctor_advice = ? WHERE id = ?", (advice_text, pid))
    conn.commit()
    conn.close()
    
    return redirect(url_for("admin_patient_view", pid=pid))

def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    _initialized_databases.add(_database_identity())
    try:
        app.run(host=os.getenv("ACL_HOST", "127.0.0.1"),
                port=_env_int("ACL_PORT", 5000, 1, 65535),
                debug=_env_bool("ACL_DEBUG"), threaded=True, use_reloader=False)
    finally:
        shutdown_resources()


if __name__ == "__main__":
    main()
