"""Exercise the real launcher from a different directory, without hardware."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


class LauncherProcessTests(unittest.TestCase):
    def test_web_starts_on_requested_port_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            environment = dict(os.environ, ACL_ENABLE_HARDWARE="0", ACL_USE_IMU="0",
                               ACL_DATABASE=str(directory / "temporary.sqlite3"),
                               ACL_EXPORT_DIR=str(directory / "exports"),
                               ACL_ADMIN_PASSWORD="", ACL_DEBUG="0",
                               ACL_SECRET_KEY="test-only-process-secret")
            log_path = directory / "server.log"
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "web.py"), "--no-browser", "--host", "127.0.0.1", "--port", str(port)],
                    cwd=directory, env=environment, stdout=log, stderr=subprocess.STDOUT,
                )
                try:
                    opener = build_opener(ProxyHandler({}))
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                                result = json.load(response)
                            self.assertEqual(result, {"status": "ok", "app": "KneeROM"})
                            break
                        except (OSError, URLError):
                            time.sleep(0.15)
                    else:
                        self.fail(f"Launcher failed to start: {log_path.read_text(encoding='utf-8', errors='replace')}")
                    with opener.open(f"http://127.0.0.1:{port}/", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn('name="csrf-token"', response.read().decode("utf-8"))
                    self.assertTrue((directory / "temporary.sqlite3").is_file())
                    self.assertFalse((directory / "database.db").exists())
                finally:
                    if os.name == "nt" and process.poll() is None:
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=10, check=False)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()