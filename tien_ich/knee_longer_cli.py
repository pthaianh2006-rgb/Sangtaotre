"""Serial knee monitor. Run with --help for connection and report options."""

import argparse
import math
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knee_core import BAUD, SerialLines, default_report_path, export_excel, parse_knee_line, preferred_serial_ports, serial_connection_error

PORT = "COM6"


def chon_cong():
    ports = preferred_serial_ports(list_ports.comports())
    if not ports:
        raise ValueError("Không tìm thấy cổng serial. Kết nối thiết bị hoặc dùng --port.")
    print("=== CHỌN CỔNG KẾT NỐI ===")
    for index, port in enumerate(ports):
        print(f"  [{index}] {port.device} ({port.description})")
    default = ports[0].device
    selection = input(f"Chọn số cổng hoặc tên cổng [Enter = {default}]: ").strip()
    if not selection:
        return default
    if selection.isdigit():
        index = int(selection)
        if index >= len(ports):
            raise ValueError("Số cổng nằm ngoài danh sách.")
        return ports[index].device
    return selection


class Keyboard:
    """Nonblocking console keys; restore terminal state on every exit path."""

    def __enter__(self):
        self.saved = None
        if sys.platform == "win32":
            import msvcrt
            self.windows = msvcrt
        elif sys.stdin.isatty():
            import termios
            import tty
            self.saved = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        return self

    def read(self):
        if sys.platform == "win32":
            if self.windows.kbhit():
                return self.windows.getwch().upper()
        elif self.saved is not None:
            import select
            if select.select([sys.stdin], [], [], 0)[0]:
                return sys.stdin.read(1).upper()
        return ""

    def __exit__(self, *args):
        if self.saved is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.saved)


def build_parser():
    parser = argparse.ArgumentParser(description="Đo góc gập đầu gối và xuất báo cáo Excel.")
    parser.add_argument("--port", help="Cổng serial, ví dụ COM6 hoặc /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=BAUD, help=f"Baud rate (mặc định {BAUD})")
    parser.add_argument("--output", type=Path, help="Đường dẫn .xlsx; mặc định tạo tên theo thời gian")
    parser.add_argument("--overwrite", action="store_true", help="Cho phép thay thế báo cáo tại --output")
    parser.add_argument("--record", action="store_true", help="Gửi L và bắt đầu ghi ngay khi kết nối")
    parser.add_argument("--duration", type=float, help="Tự dừng sau số giây này (phù hợp với --record)")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.baud <= 0:
        parser.error("--baud phải lớn hơn 0")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("--duration phải là số dương hữu hạn")
    output = args.output or default_report_path()
    if output.suffix.lower() != ".xlsx":
        parser.error("--output phải có đuôi .xlsx")
    if output.exists() and not args.overwrite:
        parser.error(f"Báo cáo đã tồn tại: {output}. Chọn tên khác hoặc dùng --overwrite.")
    rows = []
    recording = False
    recording_start = None
    connection = None
    status = 0
    try:
        port = args.port or chon_cong()
        connection = serial.Serial(port, args.baud, timeout=0.05, write_timeout=0.5)
        print(f">> Đã kết nối {port}")
        print("C = Calib; L = Ghi / tạm dừng (giữ mẫu cũ); Q hoặc Ctrl+C = thoát và xuất Excel")
        started = time.monotonic()
        last_display = 0.0
        frames = SerialLines()
        with Keyboard() as keyboard:
            if args.record:
                connection.write(b"L")
                recording = True
                recording_start = time.monotonic()
            while args.duration is None or time.monotonic() - started < args.duration:
                key = keyboard.read()
                if key in ("Q", "\x03"):
                    break
                if key in ("C", "L"):
                    connection.write(key.encode("ascii"))
                    if key == "L":
                        recording = not recording
                        if recording_start is None:
                            recording_start = time.monotonic()
                        print(f">> {'Đang ghi' if recording else 'Tạm dừng'} — {len(rows)} mẫu")
                for line in frames.feed(connection.readline(512)):
                    now = time.monotonic()
                    if line.startswith("["):
                        print(line)
                        continue
                    flex = parse_knee_line(line)
                    if flex is None:
                        continue
                    if recording:
                        rows.append((len(rows) + 1, round(now - recording_start, 3), flex))
                    if now - last_display >= 0.1:
                        print(f"  {'[GHI] ' if recording else ''}Góc gập = {flex:.1f}°; {len(rows)} mẫu")
                        last_display = now
    except (KeyboardInterrupt, EOFError):
        pass
    except (serial.SerialException, OSError, ValueError) as exc:
        detail = serial_connection_error(port, exc) if connection is None and "port" in locals() and isinstance(exc, (serial.SerialException, OSError)) else str(exc)
        print(f"!! {detail}", file=sys.stderr)
        status = 1
    finally:
        if connection is not None:
            try:
                if recording and connection.is_open:
                    connection.write(b"L")
            except (serial.SerialException, OSError):
                pass
            finally:
                connection.close()
    if rows:
        if output.exists() and not args.overwrite:
            output = default_report_path()
        ok, message = export_excel(rows, output)
        print(message, file=sys.stdout if ok else sys.stderr)
        if not ok:
            # Keep an export attempt in the current directory if the requested
            # destination is locked or unavailable.
            ok, message = export_excel(rows, default_report_path())
            print(message, file=sys.stdout if ok else sys.stderr)
            if not ok:
                status = 1
    else:
        print("Chưa có dữ liệu để xuất.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
