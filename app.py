"""Compatibility entry point for the original python app.py command."""

from web import main


if __name__ == "__main__":
    raise SystemExit(main())