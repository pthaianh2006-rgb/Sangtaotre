"""Compatibility entry point for the original python app.py command."""

from pathlib import Path
import sys

# The embedded project Python does not add the script directory automatically.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from web import main


if __name__ == "__main__":
    raise SystemExit(main())
