"""Fail-closed PostgreSQL startup probe for the KneeROM launcher."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1] / "aclproject2-main" / "aclproject2-main"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from database_layer import connect  # noqa: E402


def main() -> int:
    database_url = os.environ.get("ACL_DATABASE_URL", "")
    if not database_url:
        print("PostgreSQL configuration is missing.", file=sys.stderr)
        return 2
    connection = connect(database_url, "")
    try:
        row = connection.execute("SELECT current_database()").fetchone()
        if not row or row[0] != "kneerom":
            print("Unexpected PostgreSQL database.", file=sys.stderr)
            return 3
    finally:
        connection.close()
    print("database=postgresql status=ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
