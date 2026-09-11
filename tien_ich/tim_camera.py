# -*- coding: utf-8 -*-
"""List camera indexes and release every device after detection."""

import argparse
import os


def find_cameras(count=5, backend="auto"):
    import cv2

    backend_id = cv2.CAP_DSHOW if backend == "dshow" or (backend == "auto" and os.name == "nt") else cv2.CAP_ANY
    found = []
    for index in range(count):
        cap = cv2.VideoCapture(index, backend_id)
        try:
            if not cap.isOpened():
                print(f"  [Index {index}] khong co")
                continue
            ok, frame = cap.read()
            if ok and frame is not None:
                height, width = frame.shape[:2]
                print(f"  [Index {index}] MO DUOC - {width}x{height}")
                found.append(index)
            else:
                print(f"  [Index {index}] mo duoc nhung khong doc duoc hinh")
        finally:
            cap.release()
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="Do tim camera cho KneeROM.")
    parser.add_argument("--count", type=int, default=5, help="So camera index can thu")
    parser.add_argument("--backend", choices=("auto", "dshow", "any"), default="auto")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 32:
        parser.error("--count phai nam trong khoang 1..32")
    if args.backend == "dshow" and os.name != "nt":
        parser.error("DirectShow chi ho tro Windows")
    try:
        found = find_cameras(args.count, args.backend)
    except ImportError:
        parser.exit(1, "Thieu OpenCV. Chay: python -m pip install -r requirements.txt\n")
    if found:
        print(f"Camera su dung duoc: {found}. Dat ACL_CAMERA_SOURCE thanh index phu hop.")
    else:
        print("Khong tim thay camera. Kiem tra ket noi va quyen camera cua Windows.")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())