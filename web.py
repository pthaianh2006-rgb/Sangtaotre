# -*- coding: utf-8 -*-
"""Portable launcher: python web.py [--no-browser] [--check]."""

from __future__ import annotations

import argparse
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "aclproject2-main" / "aclproject2-main"
APP_FILE = APP_DIR / "app.py"


def select_python() -> Path:
    """Prefer the project environment, also when run from another folder."""
    relative = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    candidate = ROOT.joinpath(".venv", *relative)
    return candidate if candidate.is_file() else Path(sys.executable)


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Cong phai la so nguyen.") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Cong phai nam trong khoang 1..65535.")
    return port


def browser_url(host: str, port: int) -> str:
    host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def wait_and_open(url: str, stop: threading.Event, timeout: float = 60) -> None:
    """Check this application's HTTP response before opening the browser."""
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while not stop.wait(0.4) and time.monotonic() < deadline:
        try:
            with opener.open(f"{url}/health", timeout=1) as response:
                health = json.load(response)
            if isinstance(health, dict) and health.get("status") == "ok" and health.get("app") == "KneeROM":
                if not stop.is_set():
                    webbrowser.open(url)
                return
        except (OSError, URLError, ValueError):
            continue


def running_kneerom(url: str) -> bool:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f'{url}/health', timeout=1) as response:
            health = json.load(response)
        return isinstance(health, dict) and health.get('app') == 'KneeROM' and health.get('status') == 'ok'
    except (OSError, URLError, ValueError):
        return False


def open_managed_service(url: str, no_browser: bool) -> int:
    launcher = ROOT / 'run_kneerom_public.ps1'
    if not launcher.is_file():
        print(f'Khong tim thay launcher: {launcher}', file=sys.stderr)
        return 1
    process = subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(launcher)],
                               cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if running_kneerom(url):
            if not no_browser: webbrowser.open(url)
            print(f'KneeROM dang chay: {url}', flush=True)
            return 0
        if process.poll() is not None and process.returncode != 0:
            print('Khong khoi dong duoc dich vu. Xem D:\\bme\\logs\\launcher.status.log',file=sys.stderr)
            return 1
        time.sleep(.3)
    print('Dich vu chua san sang. Xem thu muc logs.',file=sys.stderr)
    return 1


def check_environment() -> int:
    """Check imports without opening hardware or the database."""
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    failed = False
    packages = {
        "flask": "Flask", "werkzeug": "Werkzeug", "openpyxl": "openpyxl",
        "numpy": "numpy", "serial": "pyserial", "cv2": "opencv-contrib-python",
        "mediapipe": "mediapipe", "matplotlib": "matplotlib", "docx": "python-docx",
    }
    for module_name, distribution in packages.items():
        try:
            module = importlib.import_module(module_name)
            if module_name == "mediapipe":
                from mediapipe.tasks.python.vision import PoseLandmarker
            print(f"  OK  {distribution}=={metadata.version(distribution)}")
        except Exception as exc:
            print(f"  LOI {distribution}: {exc}")
            failed = True
    if not APP_FILE.is_file():
        print(f"  LOI Khong tim thay {APP_FILE}")
        failed = True
    print("Cai lai thu vien: python -m pip install -r requirements.txt" if failed else
          "Moi truong san sang. Kiem tra camera/ESP32 rieng khi ket noi.")
    return int(failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Khoi dong KneeROM web.")
    parser.add_argument("--host", default=os.getenv("ACL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=port_number, default=os.getenv("ACL_PORT", "5001"))
    parser.add_argument("--no-browser", action="store_true", help="Khong tu mo trinh duyet")
    parser.add_argument("--check", action="store_true", help="Kiem tra thu vien, khong chay web")
    args = parser.parse_args(argv)
    interpreter = select_python()
    if interpreter.resolve() != Path(sys.executable).resolve():
        try:
            return subprocess.call([str(interpreter), str(Path(__file__).resolve()),
                                    *(sys.argv[1:] if argv is None else argv)])
        except KeyboardInterrupt:
            return 130
        except OSError as exc:
            print(f"Khong khoi dong duoc Python trong .venv: {exc}", file=sys.stderr)
            return 1
    if args.check:
        return check_environment()
    if not APP_FILE.is_file():
        parser.error(f"Khong tim thay ung dung: {APP_FILE}")
    url = browser_url(args.host, args.port)
    if running_kneerom(url):
        print(f'KneeROM da chay: {url}', flush=True)
        if not args.no_browser: webbrowser.open(url)
        return 0
    if os.name == 'nt' and args.port == 5001 and not os.getenv('ACL_DATABASE') and not os.getenv('ACL_DATABASE_URL'):
        return open_managed_service(url, args.no_browser)
    os.environ["ACL_HOST"] = args.host
    os.environ["ACL_PORT"] = str(args.port)
    url = browser_url(args.host, args.port)
    print(f"KneeROM: {url}\nNhan Ctrl+C de dung.", flush=True)
    sys.path.insert(0, str(APP_DIR))
    stop = threading.Event()
    if not args.no_browser:
        threading.Thread(target=wait_and_open, args=(url, stop), daemon=True).start()
    try:
        runpy.run_path(str(APP_FILE), run_name="__main__")
    except KeyboardInterrupt:
        return 130
    except ImportError as exc:
        print(f"Thieu/loi thu vien: {exc}. Chay python web.py --check.", file=sys.stderr)
        return 1
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
