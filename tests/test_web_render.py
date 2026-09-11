"""Render each screen with temporary accounts and validate its JavaScript."""

from html.parser import HTMLParser
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "aclproject2-main" / "aclproject2-main"


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.active = tag == "script" and "src" not in attrs and attrs.get("type", "text/javascript") in ("text/javascript", "module")
        if self.active:
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.scripts[-1] += data


class WebRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        env = {
            "ACL_DATABASE": str(Path(cls.temporary.name) / "web.sqlite3"),
            "ACL_EXPORT_DIR": str(Path(cls.temporary.name) / "exports"),
            "ACL_ENABLE_HARDWARE": "0", "ACL_USE_IMU": "0",
            "ACL_SECRET_KEY": "test-only-render-secret", "ACL_ADMIN_PASSWORD": "",
        }
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location("knee_web_render_tests", APP_DIR / "app.py")
            cls.module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.module
            spec.loader.exec_module(cls.module)
            cls.module.app.config.update(TESTING=True)
            cls.module.init_db()
        conn = cls.module.connect_db()
        try:
            conn.execute("INSERT INTO users(id,username,password,role,name,age,week) VALUES(1,'patient','unused','patient',?,30,2)", ('Bệnh nhân <script>alert(1)</script>',))
            conn.execute("INSERT INTO users(id,username,password,role,name,age,week) VALUES(2,'doctor','unused','doctor','Bác sĩ',40,0)")
            conn.executemany("INSERT INTO measurements(patient_id,angle,time,source) VALUES(1,?,?,?)", [(45, "2026-09-08 10:00:00", "camera"), (90, "2026-09-09 10:00:00", "imu")])
            conn.commit()
        finally:
            conn.close()
        cls.addClassCleanup(cls.module.shutdown_resources)
        cls.node = shutil.which("node")

    def check_javascript(self, text, label):
        if self.node:
            result = subprocess.run([self.node, "--check"], input=text, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, f"{label}: {result.stderr}")

    def test_every_screen_renders_and_inline_javascript_parses(self):
        cases = [(None, None, "/"), (1, "patient", "/dashboard"), (1, "patient", "/profile"),
                 (1, "patient", "/my_records"), (1, "patient", "/game"),
                 (2, "doctor", "/admin"), (2, "doctor", "/admin/patient/1")]
        for user_id, role, route in cases:
            with self.subTest(route=route):
                client = self.module.app.test_client()
                if user_id is not None:
                    with client.session_transaction() as session:
                        session.update(user_id=user_id, role=role, name="Test")
                response = client.get(route)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertNotIn("Bệnh nhân <script>alert(1)</script>", html)
                self.assertIn('name="csrf-token"', html)
                scripts = Scripts()
                scripts.feed(html)
                self.check_javascript("\n;\n".join(scripts.scripts), route)

    def test_static_javascript_parses(self):
        if not self.node:
            self.skipTest("Node.js is optional; install it to validate JavaScript syntax")
        for path in (APP_DIR / "static").glob("*.js"):
            with self.subTest(file=path.name):
                self.check_javascript(path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
