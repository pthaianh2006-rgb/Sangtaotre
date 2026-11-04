import importlib.util
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from io import BytesIO
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "aclproject2-main" / "aclproject2-main"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
spec = importlib.util.spec_from_file_location("acl_backend_tests", APP_DIR / "app.py")
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
with mock.patch("threading.Thread.start", side_effect=AssertionError("thread started during import")):
    spec.loader.exec_module(backend)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ACL_ADMIN_PASSWORD": ""})
        self.env.start()
        backend.app.config.update(TESTING=True, SECRET_KEY="test-secret", DATABASE=str(Path(self.tmp.name) / "test.db"), HARDWARE_ENABLED=False, CSRF_ENABLED=True)
        backend.EXPORT_DIR = ""
        backend._hardware_owner = None
        backend._hardware_last_seen = 0
        backend._reset_tracking()
        backend.imu_connected = False
        backend.init_db()
        conn = backend.connect_db()
        try:
            conn.executemany("INSERT INTO patients(id,username,password,name,age,week) VALUES(?,?,?,?,?,?)", [(1,"patient1","unused","Patient One",30,2), (2,"patient2","unused","Patient Two",35,3)])
            conn.execute("INSERT INTO doctors(id,username,password,name) VALUES(?,?,?,?)", (3,"doctor","unused","Doctor"))
            conn.commit()
        finally:
            conn.close()
        self.client = self.client_for(1)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def client_for(self, uid):
        client = backend.app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=uid, username=f"user{uid}", name="Test User", role="doctor" if uid == 3 else "patient", csrf_token="test-token")
        return client

    def post(self, path, data=None, client=None):
        return (client or self.client).post(path, json=data, headers={"X-CSRF-Token": "test-token"})

    def measured(self, value=75):
        backend._claim_hardware(1)
        backend.max_rom_session = value
        backend.camera_last_data = time.monotonic()

    def test_health_does_not_touch_database_or_devices(self):
        with mock.patch.object(backend, "init_db", side_effect=AssertionError("DB touched")):
            response = backend.app.test_client().get("/health")
        self.assertEqual((response.json["status"], response.json["app"]), ("ok", "KneeROM"))
        self.assertTrue(response.json.get("build"))
        self.assertIsNone(backend.camera)
        self.assertIsNone(backend.pose)

    def test_authentication_and_csrf(self):
        anonymous = backend.app.test_client()
        for path in ("/get_angle", "/get_imu_angle", "/imu_status", "/imu_ports", "/get_measurements"):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        self.assertEqual(self.client.post("/reset_rom/camera").status_code, 400)
        self.assertEqual(self.post("/measurement/start").status_code, 200)
        self.assertEqual(self.post("/reset_rom/camera").status_code, 200)
        self.assertEqual(self.client_for(3).get("/get_angle").status_code, 403)

    def test_hardware_lease_isolates_patients(self):
        self.measured(90)
        other = self.client_for(2)
        self.assertTrue(other.get("/get_max_rom").json["busy"])
        self.assertEqual(self.post("/save_rom/camera", client=other).status_code, 409)
        backend._hardware_last_seen -= backend.HARDWARE_LEASE_SECONDS + 1
        # Polling after expiry stays idle until the next patient explicitly starts.
        self.assertFalse(other.get("/get_max_rom").json["active"])
        self.assertEqual(self.post("/measurement/start", client=other).status_code, 200)
        response = other.get("/get_max_rom")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["max_rom"], 0)
        self.assertEqual(backend._hardware_owner, 2)

    def test_real_measurement_save_is_preserved(self):
        self.measured(75)
        response = self.post("/save_rom/camera")
        self.assertEqual(response.json["saved_rom"], 75)
        self.assertEqual(self.post("/save_rom/camera").status_code, 409)
        conn = backend.connect_db()
        try:
            self.assertEqual(conn.execute("SELECT patient_id,angle,source FROM measurements ORDER BY id").fetchall(), [(1,75,"camera")])
        finally:
            conn.close()

    def test_stale_measurement_is_not_saved(self):
        self.measured()
        backend.camera_last_data -= 5
        self.assertEqual(self.post("/save_rom/camera").status_code, 409)
        self.assertEqual(self.post("/reset_rom/bogus").status_code, 400)

    def test_removed_game_routes_and_navigation(self):
        self.measured(75)
        anonymous = backend.app.test_client()
        with anonymous.session_transaction() as session:
            session["csrf_token"] = "test-token"
        for client in (anonymous, self.client, self.client_for(3)):
            self.assertEqual(client.get("/game").status_code, 404)
            for route in ("/game_save", "/set_goal"):
                response = client.post(route, json={"rom":74,"source":"camera","goal":90}, headers={"X-CSRF-Token":"test-token"})
                self.assertEqual(response.status_code, 404)
        for route in ("/dashboard", "/profile", "/my_records", "/care"):
            html = self.client.get(route).get_data(as_text=True)
            self.assertNotIn('href="/game"', html)
        self.assertNotIn("game", backend.app.view_functions)
        self.assertNotIn("game_save", backend.app.view_functions)
        conn = backend.connect_db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0],0)
        finally:
            conn.close()

    def test_patient_cannot_delete_or_export_others(self):
        conn = backend.connect_db()
        try:
            conn.execute("INSERT INTO measurements(id,patient_id,angle,time,source) VALUES(1,2,80,?,?)", ("2026-01-01 10:00:00", "camera"))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.post("/delete_measurement/1").status_code, 403)
        self.assertEqual(self.client.get("/export/2").status_code, 403)
        self.assertEqual(self.post("/delete_all/2").status_code, 403)

    def test_empty_history_pages(self):
        for path in ("/dashboard", "/profile", "/my_records"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_imu_ignores_bad_lines_and_preserves_signed_flexion(self):
        backend._claim_hardware(1)
        for line in ("garbage", "Knee:nan", "Knee:inf", "123,nope", "[INFO] hello", "Knee:201"):
            backend._process_imu_line(line)
        self.assertEqual(backend.imu_last_data, 0)
        backend._process_imu_line("Knee:190.0 [axis]")
        self.assertEqual(backend.imu_angle, -10)
        self.assertEqual(backend.imu_max_rom, 0)
        backend._process_imu_line("1000,95.5")
        self.assertAlmostEqual(backend.imu_angle, 84.5)
        self.assertEqual(backend.imu_max_rom, 84.5)

    def test_camera_geometry_and_failure_cleanup(self):
        self.assertAlmostEqual(backend.calculate_angle((0,0), (1,0), (1,1)), 90)
        self.assertAlmostEqual(backend.calculate_angle((0,0), (1,0), (2,0)), 180)
        self.assertAlmostEqual(backend.calculate_angle((0,0), (640,360), (1280,0)), 2*math.degrees(math.atan2(640,360)))
        with self.assertRaises(ValueError):
            backend.calculate_angle((0,0), (0,0), (1,1))
        camera, pose = mock.Mock(), mock.Mock()
        camera.read.return_value = (False, None)
        backend.camera, backend.pose = camera, pose
        try:
            with mock.patch.object(backend, "_open_camera"), mock.patch.object(backend, "_hardware_active", return_value=True), mock.patch.object(backend._stop_event, "wait"), self.assertLogs(backend.logger, level="ERROR"):
                backend._camera_worker()
            self.assertEqual(camera.read.call_count, 10)
            camera.release.assert_called_once()
            pose.close.assert_called_once()
            self.assertIsNone(backend.camera)
            self.assertIsNone(backend.pose)
        finally:
            backend.camera = backend.pose = None

    def test_export_user_name_is_text(self):
        from openpyxl import load_workbook
        conn = backend.connect_db()
        try:
            conn.execute("UPDATE patients SET name=? WHERE id=1", ("=1+1",))
            conn.execute("INSERT INTO measurements(patient_id,angle,time,source) VALUES(1,75,?,?)", ("2026-01-01 10:00:00", "camera"))
            conn.commit()
        finally:
            conn.close()
        response = self.client_for(3).get("/export_all")
        workbook = load_workbook(BytesIO(response.data))
        try:
            self.assertEqual(workbook.active["A2"].value, "=1+1")
            self.assertEqual(workbook.active["A2"].data_type, "s")
        finally:
            workbook.close()
            response.close()

    def test_migration_preserves_legacy_data(self):
        legacy = str(Path(self.tmp.name) / "legacy.db")
        conn = sqlite3.connect(legacy)
        try:
            conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT, name TEXT, age INTEGER, week INTEGER, doctor_advice TEXT)")
            conn.execute("CREATE TABLE measurements(id INTEGER PRIMARY KEY, patient_id INTEGER, angle REAL, time TEXT)")
            conn.execute("INSERT INTO users VALUES(1,'old','hash','patient','Old Patient',40,1,'Advice')")
            conn.execute("INSERT INTO measurements VALUES(1,1,85,'2025-01-01 12:00:00')")
            conn.commit()
        finally:
            conn.close()
        backend.app.config["DATABASE"] = legacy
        backend.init_db()
        backend.init_db()
        conn = sqlite3.connect(legacy)
        try:
            self.assertEqual(conn.execute("SELECT angle,source FROM measurements").fetchall(), [(85,"camera")])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0], 0)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(patients)")}
            self.assertTrue({"rom_goal", "rom_goal_days", "rom_goal_start"} <= columns)
            fk_targets = {row[2] for row in conn.execute("PRAGMA foreign_key_list(measurements)")}
            self.assertEqual(fk_targets, {"patients"})
        finally:
            conn.close()

    def test_register_and_login_roundtrip(self):
        client = backend.app.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        with client.session_transaction() as session:
            token = session["csrf_token"]
        form = {"username": "new_patient", "password": "strong-password-123", "name": "Bệnh nhân mới", "age": "30", "week": "2", "role": "doctor", "csrf_token": token}
        self.assertEqual(client.post("/register", data=form).status_code, 200)
        conn = backend.connect_db()
        try:
            password_hash = conn.execute("SELECT password FROM patients WHERE username='new_patient'").fetchone()[0]
            self.assertNotEqual(password_hash, form["password"])
            self.assertIsNone(conn.execute("SELECT id FROM doctors WHERE username='new_patient'").fetchone())
        finally:
            conn.close()
        response = client.post("/login/patient", data={"username": form["username"], "password": form["password"], "csrf_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/dashboard"))
        self.assertEqual(client.get("/dashboard").status_code, 200)
        with client.session_transaction() as session:
            self.assertNotEqual(session["csrf_token"], token)

    def test_doctor_account_management_hashes_passwords(self):
        doctor = self.client_for(3)
        with doctor.session_transaction() as session:
            session["username"] = "admin"
        form = {"username": "doctor_two", "name": "Doctor Two",
                "password": "strong-password-123", "csrf_token": "test-token"}
        response = doctor.post("/admin/doctors/create", data=form)
        self.assertEqual(response.status_code, 302)
        conn = backend.connect_db()
        try:
            row = conn.execute("SELECT id,password FROM doctors WHERE username=?",
                               ("doctor_two",)).fetchone()
            self.assertIsNotNone(row)
            self.assertNotEqual(row[1], form["password"])
            doctor_id = row[0]
        finally:
            conn.close()
        response = doctor.post(f"/admin/doctors/{doctor_id}/password", data={
            "password": "replacement-password-456", "csrf_token": "test-token"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(doctor.post("/admin/doctors/3/delete", data={
            "csrf_token": "test-token"}).status_code, 302)
        conn = backend.connect_db()
        try:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM doctors WHERE id=3").fetchone())
        finally:
            conn.close()


    def test_patient_and_doctor_use_separate_login_forms_and_tables(self):
        from werkzeug.security import generate_password_hash

        conn = backend.connect_db()
        try:
            conn.execute("UPDATE patients SET password=? WHERE id=1", (generate_password_hash("patient-password"),))
            conn.execute("UPDATE doctors SET password=? WHERE id=3", (generate_password_hash("doctor-password"),))
            conn.commit()
        finally:
            conn.close()

        page_client = backend.app.test_client()
        page = page_client.get("/").get_data(as_text=True)
        self.assertIn('action="/login/patient"', page)
        self.assertIn('action="/login/doctor"', page)
        with page_client.session_transaction() as session:
            token = session["csrf_token"]

        wrong_portal = page_client.post("/login/doctor", data={
            "username": "patient1", "password": "patient-password", "csrf_token": token,
        })
        self.assertEqual(wrong_portal.status_code, 400)

        doctor_login = page_client.post("/login/doctor", data={
            "username": "doctor", "password": "doctor-password", "csrf_token": token,
        })
        self.assertEqual(doctor_login.status_code, 302)
        self.assertTrue(doctor_login.location.endswith("/admin"))
        with page_client.session_transaction() as session:
            self.assertEqual(session["role"], "doctor")



if __name__ == "__main__":
    unittest.main()
