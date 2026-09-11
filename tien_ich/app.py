"""Compatibility entry point; prefer python web.py from the project root."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(ROOT))
    from web import main as run_web
    return run_web()


if __name__ == "__main__":
    raise SystemExit(main())