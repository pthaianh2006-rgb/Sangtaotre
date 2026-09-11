# -*- coding: utf-8 -*-
# GUI do goc gap dau goi - giao dien y te / lam sang.
# Chay:  python knee_longer.py
#
# - Chon cong COM -> Ket noi
# - Nut CALIB (C) = dat moc duoi thang
# - Nut GHI (L)   = bat/tat ghi du lieu
# - Do thi + so goc gap cap nhat theo thoi gian thuc
# - Nut XUAT EXCEL = luu knee_data.xlsx (kem bieu do + thong ke)

import os
import sys
import time
import queue
import ctypes
import threading
from collections import deque

import serial
from serial.tools import list_ports

from knee_core import BAUD, OUT, TARGET, SerialLines, default_report_path, export_excel, parse_knee_line, preferred_serial_ports, serial_connection_error

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.patches import Polygon
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

DEFAULT_PORT = "COM6"

# ---------- Bang mau (Meaxtio - warm cream + lavender) ----------
BG = "#f5efe6"          # nen kem am
CARD = "#ffffff"        # the trang
BORDER = "#ece3d6"      # vien the (be nhat)
INK = "#211c33"         # chu chinh (den tim dam)
MUTED = "#6f6a5e"       # chu phu (xam am dam hon - de doc)
PRIMARY = "#7c6ae0"     # tim lavender (chu dao)
PRIMARY_DK = "#6553c4"
TAN = "#c6a56c"         # nau vang phu (accent)
HEADER = "#211c33"      # thanh tieu de (den tim)
GOOD = "#38a18b"        # dat muc tieu (xanh am)
WARN = "#d19a4e"        # canh bao (tan/amber)
BAD = "#e06a5a"         # loi / rung (coral am)
TRACK = "#f6f1ea"       # nen nhat cho vung phu


# =====================================================================
#  Ung dung GUI - giao dien y te
# =====================================================================
class KneeApp:
    def __init__(self, root):
        self.root = root
        root.title("KneeROM — Hệ thống theo dõi phục hồi chức năng khớp gối")
        root.configure(bg=BG)

        # ---- Ti le theo DPI man hinh (chu + layout net & dung kich thuoc) ----
        dpi = root.winfo_fpixels("1i")
        self.scale = max(1.0, dpi / 96.0)
        try:
            root.tk.call("tk", "scaling", dpi / 72.0)   # font point -> pixel dung DPI
        except Exception:
            pass
        root.geometry(f"{self._s(1060)}x{self._s(720)}")
        root.minsize(self._s(940), self._s(640))

        self.ser = None
        self.reader_thread = None
        self.stop_flag = threading.Event()
        self.msg_q = queue.Queue(maxsize=4096)
        self.export_q = queue.Queue()
        self._closing = False
        self._exporting = False
        self._saved_count = 0
        self._session_id = 0
        self._rom = None
        self._plot_dirty = False

        self.recording = False
        self.rows = []             # (stt, t, flex) cho xuat Excel
        self.t0 = None
        self.cur_flex = None

        self.buf_t = deque(maxlen=400)
        self.buf_v = deque(maxlen=400)
        self.conn_t0 = 0.0
        self._n_samples = 0
        self._bias_active = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._pump_id = self.root.after(60, self._pump)
        self._plot_id = self.root.after(120, self._refresh_plot)

    # ---------------- Tien ich UI ----------------
    def _s(self, px):
        """Doi so pixel thiet ke sang pixel thuc theo DPI."""
        return int(round(px * self.scale))

    def _card(self, parent, pad=16):
        """Khung the trang co vien mong (kieu benh an)."""
        outer = tk.Frame(parent, bg=BORDER)
        inner = tk.Frame(outer, bg=CARD, padx=pad, pady=pad)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return outer, inner

    def _section_title(self, parent, text):
        tk.Label(parent, text=text.upper(), bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 8))

    # ---------------- Dung giao dien ----------------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Med.TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=INK, bordercolor=BORDER, arrowcolor=PRIMARY)

        # Font hinh hoc bo tron (giong Poppins) cho tieu de/so; fallback neu thieu.
        fams = set(tkfont.families())
        for cand in ("Poppins", "Century Gothic", "Segoe UI Semibold"):
            if cand in fams:
                self.f_display = cand
                break
        else:
            self.f_display = "Segoe UI"

        # ===== Thanh tieu de =====
        header = tk.Frame(self.root, bg=HEADER, height=self._s(64))
        header.pack(fill="x")
        header.pack_propagate(False)

        badge = tk.Label(header, text="✚", bg=PRIMARY, fg="#ffffff",
                         font=("Segoe UI", 16, "bold"), width=3)
        badge.pack(side="left", padx=(16, 12), pady=12)
        titlebox = tk.Frame(header, bg=HEADER)
        titlebox.pack(side="left", pady=10)
        tk.Label(titlebox, text="KneeROM Monitor", bg=HEADER, fg="#ffffff",
                 font=(self.f_display, 15, "bold")).pack(anchor="w")
        tk.Label(titlebox, text="Theo dõi tầm vận động khớp gối · Phục hồi chức năng",
                 bg=HEADER, fg="#b9aef0", font=("Segoe UI", 9)).pack(anchor="w")

        # den trang thai o goc phai header
        self.hdr_status = tk.Label(header, text="●  CHƯA KẾT NỐI", bg=HEADER, fg="#ff9aa0",
                                   font=("Segoe UI", 10, "bold"))
        self.hdr_status.pack(side="right", padx=18)

        # ===== Thanh ket noi =====
        connbar_o, connbar = self._card(self.root, pad=10)
        connbar_o.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(connbar, text="Cổng kết nối", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(connbar, textvariable=self.port_var, width=30,
                                    state="readonly", style="Med.TCombobox")
        self.port_cb.pack(side="left", padx=(10, 6))
        self._scan_ports()
        tk.Button(connbar, text="⟳ Quét", command=self._scan_ports, bg=TRACK, fg=INK,
                  activebackground=BORDER, relief="flat", font=("Segoe UI", 9),
                  padx=10, pady=4, cursor="hand2").pack(side="left")
        self.btn_conn = tk.Button(connbar, text="Kết nối", command=self.toggle_connect,
                                  bg=PRIMARY, fg="white", activebackground=PRIMARY_DK,
                                  relief="flat", font=("Segoe UI", 10, "bold"),
                                  padx=22, pady=5, cursor="hand2")
        self.btn_conn.pack(side="left", padx=12)

        # ===== Than chinh =====
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=16, pady=14)

        # ----- Cot trai -----
        left = tk.Frame(main, bg=BG, width=self._s(300))
        left.pack(side="left", fill="y", padx=(0, 14))
        left.pack_propagate(False)

        # The so goc gap (co dai mau ben trai)
        read_o, read = self._card(left, pad=0)
        read_o.pack(fill="x")
        self.accent_bar = tk.Frame(read, bg=PRIMARY, width=6)
        self.accent_bar.pack(side="left", fill="y")
        rpad = tk.Frame(read, bg=CARD, padx=20, pady=18)
        rpad.pack(side="left", fill="both", expand=True)
        tk.Label(rpad, text="GÓC GẬP HIỆN TẠI", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        arow = tk.Frame(rpad, bg=CARD)
        arow.pack(anchor="w", pady=(2, 0))
        self.angle_lbl = tk.Label(arow, text="—", bg=CARD, fg=INK,
                                  font=(self.f_display, 50, "bold"))
        self.angle_lbl.pack(side="left")
        tk.Label(arow, text="°", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 22)).pack(side="left", anchor="n", pady=(10, 0))
        # pill trang thai muc tieu
        self.pill = tk.Label(rpad, text="Chưa có tín hiệu", bg=TRACK, fg=MUTED,
                             font=("Segoe UI", 9, "bold"), padx=10, pady=3)
        self.pill.pack(anchor="w", pady=(6, 0))

        # The chi so phien
        stat_o, stat = self._card(left, pad=16)
        stat_o.pack(fill="x", pady=(14, 0))
        self._section_title(stat, "Chỉ số buổi đo")
        self.rom_lbl = self._stat_row(stat, "ROM (góc lớn nhất)", "— °")
        self.pct_lbl = self._stat_row(stat, "% đạt mục tiêu 90°", "— %")
        self.count_lbl = self._stat_row(stat, "Số mẫu ghi", "0")
        self.hz_lbl = self._stat_row(stat, "Tốc độ lấy mẫu", "— Hz", last=True)

        # The dieu khien
        ctrl_o, ctrl = self._card(left, pad=16)
        ctrl_o.pack(fill="x", pady=(14, 0))
        self._section_title(ctrl, "Điều khiển")
        self.btn_calib = tk.Button(ctrl, text="⊕  CALIB — Duỗi thẳng", command=self.send_calib,
                                   bg=TRACK, fg=INK, activebackground=BORDER, relief="flat",
                                   font=("Segoe UI", 10, "bold"), pady=9, state="disabled",
                                   cursor="hand2")
        self.btn_calib.pack(fill="x", pady=(0, 8))
        self.btn_rec = tk.Button(ctrl, text="▶  BẮT ĐẦU GHI", command=self.toggle_record,
                                 bg=GOOD, fg="white", activebackground="#178245", relief="flat",
                                 font=("Segoe UI", 11, "bold"), pady=11, state="disabled",
                                 cursor="hand2")
        self.btn_rec.pack(fill="x", pady=(0, 8))
        self.btn_export = tk.Button(ctrl, text="⭳  XUẤT BÁO CÁO EXCEL", command=self.do_export,
                                    bg=PRIMARY, fg="white", activebackground=PRIMARY_DK,
                                    relief="flat", font=("Segoe UI", 10, "bold"), pady=9,
                                    cursor="hand2")
        self.btn_export.pack(fill="x")
        self.btn_new = tk.Button(ctrl, text="Phiên đo mới", command=self.new_session,
                                 bg=TRACK, fg=INK, relief="flat", pady=5,
                                 font=("Segoe UI", 9), cursor="hand2")
        self.btn_new.pack(fill="x", pady=(6, 0))

        # ----- Cot phai: do thi + nhat ky -----
        right = tk.Frame(main, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        plot_o, plot = self._card(right, pad=12)
        plot_o.pack(fill="both", expand=True)
        self._section_title(plot, "Biểu đồ góc gập theo thời gian thực")
        self.fig = Figure(figsize=(6, 3.0), dpi=int(100 * self.scale), facecolor=CARD)
        self.ax = self.fig.add_subplot(111)
        self._style_axes()
        self.plot_line, = self.ax.plot([], [], color=PRIMARY, linewidth=2.2)
        self.plot_fill = Polygon([(0, -20), (0, -20), (0, -20)], closed=True,
                                 facecolor=PRIMARY, edgecolor="none", alpha=0.08, visible=False)
        self.ax.add_patch(self.plot_fill)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        log_o, log = self._card(right, pad=12)
        log_o.pack(fill="x", pady=(14, 0))
        self._section_title(log, "Nhật ký lâm sàng")
        self.log = tk.Text(log, height=6, bg=TRACK, fg=INK, relief="flat",
                           font=("Consolas", 9), wrap="word", padx=8, pady=6,
                           highlightthickness=0)
        self.log.pack(fill="x")
        self._log("San sang. Chon cong COM va bam Ket noi de bat dau.")

    def _stat_row(self, parent, label, value, last=False):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(4, 4))
        tk.Label(row, text=label, bg=CARD, fg=MUTED, font=("Segoe UI", 10)).pack(side="left")
        val = tk.Label(row, text=value, bg=CARD, fg=INK, font=("Segoe UI", 12, "bold"))
        val.pack(side="right")
        if not last:
            tk.Frame(parent, bg=BORDER, height=1).pack(fill="x")
        return val

    def _style_axes(self):
        self.ax.clear()
        self.ax.set_facecolor(CARD)
        self.ax.tick_params(colors=MUTED, labelsize=8)
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)
        for name in ("left", "bottom"):
            self.ax.spines[name].set_color(BORDER)
        self.ax.set_xlabel("Thời gian (giây)", color=MUTED, fontsize=9)
        self.ax.set_ylabel("Góc gập (độ)", color=MUTED, fontsize=9)
        self.ax.set_ylim(-5, 105)
        self.ax.grid(True, color="#efe8de", linewidth=0.9)
        self.ax.axhline(TARGET, color=BAD, linestyle="--", linewidth=1.2)
        self.ax.text(0.0, TARGET + 1.5, "Mục tiêu 90°", color=BAD, fontsize=8,
                     transform=self.ax.get_yaxis_transform(), va="bottom")

    # ---------------- Cong / ket noi ----------------
    def _scan_ports(self):
        ports = preferred_serial_ports(list_ports.comports())
        items = [f"{port.device}  ({port.description})" for port in ports]
        self._port_map = {label: port.device for label, port in zip(items, ports)}
        self.port_cb["values"] = items
        if not items:
            self.port_var.set("")
        elif self.port_var.get() not in items:
            self.port_cb.current(0)

    def toggle_connect(self):
        if self.ser is not None:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        if self.reader_thread is not None and self.reader_thread.is_alive():
            self._log("Đang chờ luồng serial cũ dừng; vui lòng thử lại.")
            return
        port = self._port_map.get(self.port_var.get())
        if not port:
            self._scan_ports()
            port = self._port_map.get(self.port_var.get())
        if not port:
            messagebox.showerror("Chưa thấy thiết bị", "Không tìm thấy cổng serial. Cắm USB rồi bấm Quét.")
            return
        try:
            connection = serial.Serial(port, BAUD, timeout=0.05, write_timeout=0.5)
        except (serial.SerialException, OSError) as exc:
            messagebox.showerror("Lỗi kết nối", serial_connection_error(port, exc))
            return
        self.ser = connection
        # Each connection owns its stop event and queue. Old workers cannot read
        # a newly opened port or publish into its stream after reconnecting.
        self.stop_flag = threading.Event()
        self.msg_q = queue.Queue(maxsize=4096)
        self.conn_t0 = time.monotonic()
        self._n_samples = 0
        self.buf_t.clear()
        self.buf_v.clear()
        self.cur_flex = None
        self._bias_active = False
        self._plot_dirty = True
        self.reader_thread = threading.Thread(
            target=self._reader_loop, args=(connection, self.stop_flag, self.msg_q),
            name="knee-serial-reader", daemon=True,
        )
        try:
            self.reader_thread.start()
        except RuntimeError as exc:
            self.stop_flag.set()
            connection.close()
            self.ser = None
            self.reader_thread = None
            messagebox.showerror("Lỗi kết nối", f"Không khởi động được luồng đọc {port}: {exc}")
            return
        self.btn_conn.config(text="Ngắt kết nối", bg=BAD, activebackground="#b83a44")
        self.hdr_status.config(text=f"●  ĐÃ KẾT NỐI · {port}", fg="#8ff0b0")
        self.btn_calib.config(state="normal")
        self.btn_rec.config(state="normal")
        self.port_cb.config(state="disabled")
        self.angle_lbl.config(text="—", fg=MUTED)
        self.pill.config(text="Đang chờ dữ liệu", fg=MUTED, bg=TRACK)
        self.hz_lbl.config(text="— Hz")
        self._log(f">> Đã kết nối {port}")

    def _disconnect(self):
        connection, self.ser = self.ser, None
        self.stop_flag.set()
        if connection is not None:
            try:
                if self.recording and connection.is_open:
                    connection.write(b"L")
            except (serial.SerialException, OSError):
                pass
            try:
                connection.close()
            except (serial.SerialException, OSError):
                pass
        if self.reader_thread is not None:
            self.reader_thread.join(timeout=0.75)
        self.recording = False
        self._drain_messages(unlimited=True)
        self.btn_rec.config(text="▶  TIẾP TỤC GHI" if self.rows else "▶  BẮT ĐẦU GHI", bg=GOOD, activebackground="#178245", state="disabled")
        self.btn_conn.config(text="Kết nối", bg=PRIMARY, activebackground=PRIMARY_DK)
        self.hdr_status.config(text="●  CHƯA KẾT NỐI", fg="#ff9aa0")
        self.btn_calib.config(state="disabled")
        self.port_cb.config(state="readonly")
        self.pill.config(text="Đã ngắt kết nối", fg=MUTED, bg=TRACK)
        self._log(">> Đã ngắt kết nối. Dữ liệu đã ghi được giữ lại.")

    def _reader_loop(self, connection, stop, messages):
        frames = SerialLines()
        failure = None
        waiting_for_calibration = False

        def publish(item):
            while not stop.is_set():
                try:
                    messages.put(item, timeout=0.05)
                    return True
                except queue.Full:
                    continue
            return False

        try:
            while not stop.is_set():
                for line in frames.feed(connection.readline(512)):
                    received_at = time.monotonic()
                    if line.lower() in ("... send c", "send c"):
                        if not waiting_for_calibration:
                            if not publish(("log", "Thiết bị đang chờ lệnh C (... send C). Bấm CALIB khi đã sẵn sàng.")):
                                return
                            waiting_for_calibration = True
                        continue
                    if line.startswith("["):
                        if not publish(("log", ">>> " + line[7:].strip() if line.startswith("[ALIGN]") else line)):
                            return
                        continue
                    flex = parse_knee_line(line)
                    if flex is not None:
                        waiting_for_calibration = False
                        # Capture recording membership before queuing: a slow UI
                        # must not shift timestamps or change which samples count.
                        session = self._session_id if self.recording else None
                        if not publish(("flex", (flex, received_at, session))):
                            return
        except (serial.SerialException, OSError) as exc:
            if not stop.is_set():
                failure = str(exc)
        finally:
            # Release the OS handle even if the GUI is busy or its event loop stopped.
            try:
                connection.close()
            except (serial.SerialException, OSError):
                pass
        if failure is not None:
            publish(("disconnected", failure))

    # ---------------- Lenh dieu khien ----------------
    def _write(self, command):
        if self.ser is None or not self.ser.is_open:
            return False
        try:
            self.ser.write(command.encode("ascii"))
            return True
        except (serial.SerialException, OSError) as exc:
            self._log(f"!! Không gửi được lệnh {command}: {exc}")
            self._disconnect()
            return False

    def send_calib(self):
        self._write("C")

    def toggle_record(self):
        if not self._write("L"):
            return
        if not self.recording:
            if self.t0 is None:
                self.t0 = time.monotonic()
            self.recording = True
            self.btn_rec.config(text="■  TẠM DỪNG GHI", bg=WARN, activebackground="#c07f1f")
            self._log(">> Đang ghi. Các mẫu trước đó được giữ lại.")
        else:
            self.recording = False
            self._drain_messages(unlimited=True)
            self.btn_rec.config(text="▶  TIẾP TỤC GHI", bg=GOOD, activebackground="#178245")
            self._log(f">> Tạm dừng ghi: {len(self.rows)} mẫu.")

    def new_session(self):
        if self._exporting:
            return
        if self.recording:
            self.toggle_record()
        self._drain_messages(unlimited=True)
        if len(self.rows) > self._saved_count:
            messagebox.showwarning("Còn dữ liệu chưa lưu", "Hãy xuất báo cáo trước khi tạo phiên mới.")
            return
        self._session_id += 1
        self.rows.clear()
        self.t0 = None
        self._rom = None
        self._saved_count = 0
        self.count_lbl.config(text="0")
        self.rom_lbl.config(text="— °")
        self.pct_lbl.config(text="— %")
        self.btn_rec.config(text="▶  BẮT ĐẦU GHI")
        self._log(">> Đã tạo phiên đo mới.")

    def do_export(self, on_success=None):
        if self._exporting:
            return False
        self._drain_messages(unlimited=True)
        if not self.rows:
            messagebox.showwarning("Chưa có dữ liệu", "Chưa ghi được mẫu nào để xuất.")
            return False
        out = filedialog.asksaveasfilename(
            title="Lưu báo cáo đo góc gập", defaultextension=".xlsx",
            filetypes=[("Báo cáo Excel", "*.xlsx")],
            initialfile=default_report_path().name,
        )
        if not out:
            return False
        snapshot = tuple(self.rows)
        self._exporting = True
        self.btn_export.config(state="disabled", text="ĐANG LƯU BÁO CÁO…")
        self.btn_new.config(state="disabled")

        def save():
            try:
                ok, message = export_excel(snapshot, out)
            except Exception as exc:
                ok, message = False, f"Không thể xuất báo cáo: {exc}"
            self.export_q.put((ok, message, len(snapshot), on_success))

        threading.Thread(target=save, name="knee-report-export", daemon=True).start()
        return True

    # ---------------- Vong lap GUI ----------------
    def _drain_messages(self, unlimited=False):
        deadline = time.monotonic() + 0.012
        changed = False
        processed = 0
        budget = self.msg_q.qsize() if unlimited else 400
        while processed < budget and (unlimited or time.monotonic() < deadline):
            try:
                kind, payload = self.msg_q.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if kind == "log":
                self._on_log(payload)
            elif kind == "flex":
                self._on_flex(*payload)
                changed = True
            elif kind == "disconnected" and self.ser is not None:
                self._log(f"!! Mất kết nối serial: {payload}")
                self._disconnect()
        if changed:
            self._render_measurement()

    def _pump(self):
        if self._closing:
            return
        self._drain_messages()
        try:
            ok, message, saved_count, callback = self.export_q.get_nowait()
        except queue.Empty:
            pass
        else:
            self._exporting = False
            self.btn_export.config(state="normal", text="⭳  XUẤT BÁO CÁO EXCEL")
            self.btn_new.config(state="normal")
            self._log((">> " if ok else "!! ") + message)
            if ok:
                self._saved_count = saved_count
                if callback:
                    callback()
                else:
                    messagebox.showinfo("Xuất Excel", message)
            else:
                messagebox.showerror("Lỗi xuất Excel", message)
        if not self._closing:
            self._pump_id = self.root.after(15 if not self.msg_q.empty() else 60, self._pump)

    def _on_flex(self, flex, received_at, session=None):
        self.cur_flex = flex
        self._n_samples += 1
        self.buf_t.append(max(0, received_at - self.conn_t0))
        self.buf_v.append(flex)
        self._plot_dirty = True
        if session == self._session_id and self.t0 is not None and received_at >= self.t0:
            self.rows.append((len(self.rows) + 1, round(received_at - self.t0, 3), flex))
            self._rom = flex if self._rom is None else max(self._rom, flex)

    def _render_measurement(self):
        flex = self.cur_flex
        if flex is None:
            return
        if not self._bias_active:
            if flex >= TARGET:
                color, text, background = GOOD, "Đạt mục tiêu", "#e3f6ea"
            elif flex >= TARGET * 0.6:
                color, text, background = WARN, "Đang gập tốt", "#fbf1df"
            else:
                color, text, background = INK, "Gần duỗi thẳng", TRACK
            self.angle_lbl.config(text=f"{flex:.1f}", fg=color)
            self.accent_bar.config(bg=color)
            self.pill.config(text=text, fg=color, bg=background)
        elapsed = self.buf_t[-1] if self.buf_t else 0
        if elapsed > 0.5:
            self.hz_lbl.config(text=f"{self._n_samples / elapsed:.1f} Hz")
        self.count_lbl.config(text=str(len(self.rows)))
        if self._rom is not None:
            self.rom_lbl.config(text=f"{self._rom:.1f} °")
            self.pct_lbl.config(text=f"{self._rom / TARGET * 100:.0f} %")

    def _refresh_plot(self):
        if self._closing:
            return
        if self._plot_dirty:
            xs, ys = list(self.buf_t), list(self.buf_v)
            self.plot_line.set_data(xs, ys)
            if xs:
                self.plot_fill.set_xy([(xs[0], -20), *zip(xs, ys), (xs[-1], -20)])
                self.plot_fill.set_visible(True)
                self.ax.set_xlim(max(0, xs[-1] - 30), max(30, xs[-1]))
                self.ax.set_ylim(min(-5, min(ys) - 5), max(105, max(ys) + 5))
            else:
                self.plot_fill.set_visible(False)
                self.ax.set_xlim(0, 30)
            self.canvas.draw_idle()
            self._plot_dirty = False
        self._plot_id = self.root.after(150, self._refresh_plot)

    def _on_log(self, text):
        low = text.lower()
        if "đang chờ lệnh c" in low:
            self.pill.config(text="Chờ hiệu chỉnh — bấm CALIB (C)", fg=WARN, bg="#fbf1df")
        elif "co rung" in low:
            self._bias_state("shaking")
        elif "giu yen" in low:
            self._bias_state("checking")
        elif "bias ok" in low:
            self._bias_state("ok")
        elif "bias chua chuan" in low:
            self._bias_state("fail")
        self._log(text)

    def _bias_state(self, state):
        self._bias_active = state in ("checking", "shaking")
        styles = {
            "checking": ("KIỂM TRA ĐỨNG YÊN — GIỮ YÊN CẢM BIẾN", PRIMARY, "#ece8fb"),
            "shaking": ("PHÁT HIỆN RUNG — GIỮ YÊN HƠN", WARN, "#fbf1df"),
            "ok": ("ĐÃ ĐỨNG YÊN — ĐÃ SET BIAS", GOOD, "#e4f4ee"),
            "fail": ("BIAS CHƯA CHUẨN — KHỞI ĐỘNG LẠI", BAD, "#fbe8e3"),
        }
        text, color, background = styles[state]
        if self._bias_active:
            self.angle_lbl.config(text="—", fg=MUTED)
        self.pill.config(text=text, fg=color, bg=background)
        self.accent_bar.config(bg=color)

    def _log(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{timestamp}] {text}\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 500:
            self.log.delete("1.0", f"{lines - 500}.0")
        self.log.see("end")

    def on_close(self):
        if self._exporting:
            messagebox.showinfo("Đang lưu", "Vui lòng đợi báo cáo được lưu xong trước khi đóng.")
            return
        if self.ser is not None:
            self._disconnect()
        if len(self.rows) > self._saved_count:
            save = messagebox.askyesnocancel("Dữ liệu chưa lưu", "Lưu báo cáo trước khi đóng ứng dụng?")
            if save is None:
                return
            if save:
                self.do_export(on_success=self._finish_close)
                return
        self._finish_close()

    def _finish_close(self):
        self._closing = True
        self.stop_flag.set()
        for callback in (self._pump_id, self._plot_id):
            if callback:
                self.root.after_cancel(callback)
        self.root.destroy()



def enable_dpi_awareness():
    """Bat DPI-aware truoc khi tao cua so -> chu net, khong bi Windows phong nhoe."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    KneeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
