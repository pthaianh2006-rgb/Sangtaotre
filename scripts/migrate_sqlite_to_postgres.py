"""Transactional KneeROM SQLite to PostgreSQL migration.

The source is always opened read-only.  The destination must already be empty;
all copied rows are verified by count and deterministic SHA-256 digest before
the PostgreSQL transaction is committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PROJECT_DIR = Path(__file__).resolve().parents[1] / "aclproject2-main" / "aclproject2-main"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from database_layer import connect, ensure_schema  # noqa: E402


PATIENT_COLUMNS = (
    "id", "username", "password", "name", "age", "week",
    "doctor_advice", "rom_goal", "rom_goal_days", "rom_goal_start",
)
DOCTOR_COLUMNS = ("id", "username", "password", "name")
MEASUREMENT_COLUMNS = ("id", "patient_id", "angle", "time", "source")


@dataclass(frozen=True)
class TableVerification:
    source_count: int
    destination_count: int
    source_digest: str
    destination_digest: str


@dataclass(frozen=True)
class MigrationReport:
    patients: TableVerification
    doctors: TableVerification
    measurements: TableVerification

    @property
    def total_rows(self) -> int:
        return sum(
            item.destination_count
            for item in (self.patients, self.doctors, self.measurements)
        )


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _select_with_defaults(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    defaults: dict[str, str],
    where: str = "",
) -> list[tuple[Any, ...]]:
    available = _columns(connection, table)
    expressions = [
        f'"{column}"' if column in available else f"{defaults[column]} AS \"{column}\""
        for column in columns
    ]
    sql = f'SELECT {", ".join(expressions)} FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    sql += ' ORDER BY "id"'
    return [tuple(row) for row in connection.execute(sql).fetchall()]


def _read_source(sqlite_path: Path) -> dict[str, list[tuple[Any, ...]]]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {sqlite_path}")

    uri = f"file:{sqlite_path.resolve().as_posix()}?mode=ro"
    source = sqlite3.connect(uri, uri=True)
    try:
        source.execute("PRAGMA query_only = ON")
        if _table_exists(source, "patients") and _table_exists(source, "doctors"):
            patients = _select_with_defaults(
                source,
                "patients",
                PATIENT_COLUMNS,
                {
                    "doctor_advice": "''",
                    "rom_goal": "NULL",
                    "rom_goal_days": "NULL",
                    "rom_goal_start": "NULL",
                },
            )
            doctors = _select_with_defaults(source, "doctors", DOCTOR_COLUMNS, {})
        elif _table_exists(source, "users"):
            user_columns = _columns(source, "users")
            if "role" not in user_columns:
                raise ValueError("Legacy users table has no role column")
            patients = _select_with_defaults(
                source,
                "users",
                PATIENT_COLUMNS,
                {
                    "doctor_advice": "''",
                    "rom_goal": "NULL",
                    "rom_goal_days": "NULL",
                    "rom_goal_start": "NULL",
                },
                "role='patient'",
            )
            doctors = _select_with_defaults(
                source, "users", DOCTOR_COLUMNS, {}, "role='doctor'"
            )
        else:
            raise ValueError("SQLite source has neither split accounts nor legacy users")

        if not _table_exists(source, "measurements"):
            measurements: list[tuple[Any, ...]] = []
        else:
            measurements = _select_with_defaults(
                source,
                "measurements",
                MEASUREMENT_COLUMNS,
                {"source": "'camera'"},
            )

        patient_ids = {int(row[0]) for row in patients}
        orphan_ids = sorted({int(row[1]) for row in measurements} - patient_ids)
        if orphan_ids:
            raise ValueError(
                "Measurements reference non-patient account IDs: "
                + ", ".join(map(str, orphan_ids))
            )
        return {
            "patients": patients,
            "doctors": doctors,
            "measurements": measurements,
        }
    finally:
        source.close()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, float):
        return format(value, ".17g")
    if isinstance(value, bytes):
        return value.hex()
    return value


def digest_rows(rows: list[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        payload = json.dumps(
            [_canonical_value(value) for value in row],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(payload)
        digest.update(b"\n")
    return digest.hexdigest()


def _fetch_destination(destination: Any) -> dict[str, list[tuple[Any, ...]]]:
    result: dict[str, list[tuple[Any, ...]]] = {}
    for table, columns in (
        ("patients", PATIENT_COLUMNS),
        ("doctors", DOCTOR_COLUMNS),
        ("measurements", MEASUREMENT_COLUMNS),
    ):
        result[table] = destination.execute(
            f'SELECT {", ".join(columns)} FROM {table} ORDER BY id'
        ).fetchall()
    return result


def _assert_empty(destination: Any) -> None:
    counts = {
        table: int(destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("patients", "doctors", "measurements")
    }
    occupied = [f"{table}={count}" for table, count in counts.items() if count]
    if occupied:
        raise ValueError("PostgreSQL destination is not empty: " + ", ".join(occupied))


def _insert_rows(destination: Any, data: dict[str, list[tuple[Any, ...]]]) -> None:
    destination.executemany(
        """
        INSERT INTO patients(
            id, username, password, name, age, week, doctor_advice,
            rom_goal, rom_goal_days, rom_goal_start
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        data["patients"],
    )
    destination.executemany(
        "INSERT INTO doctors(id, username, password, name) VALUES (?, ?, ?, ?)",
        data["doctors"],
    )
    destination.executemany(
        """
        INSERT INTO measurements(id, patient_id, angle, time, source)
        VALUES (?, ?, ?, ?, ?)
        """,
        data["measurements"],
    )


def _reset_identities(destination: Any) -> None:
    for table in ("patients", "doctors", "measurements"):
        destination.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', 'id'),
                COALESCE(MAX(id), 1),
                MAX(id) IS NOT NULL
            ) FROM {table}
            """
        )


def _verify_foreign_keys(destination: Any) -> None:
    orphan = destination.execute(
        """
        SELECT m.id FROM measurements m
        LEFT JOIN patients p ON p.id = m.patient_id
        WHERE p.id IS NULL LIMIT 1
        """
    ).fetchone()
    if orphan is not None:
        raise ValueError(f"PostgreSQL foreign-key verification failed at measurement {orphan[0]}")


def _build_report(
    source: dict[str, list[tuple[Any, ...]]],
    destination: dict[str, list[tuple[Any, ...]]],
) -> MigrationReport:
    checks: dict[str, TableVerification] = {}
    for table in ("patients", "doctors", "measurements"):
        source_digest = digest_rows(source[table])
        destination_digest = digest_rows(destination[table])
        checks[table] = TableVerification(
            source_count=len(source[table]),
            destination_count=len(destination[table]),
            source_digest=source_digest,
            destination_digest=destination_digest,
        )
        if len(source[table]) != len(destination[table]):
            raise ValueError(f"Row-count verification failed for {table}")
        if source_digest != destination_digest:
            raise ValueError(f"Content-digest verification failed for {table}")
    return MigrationReport(**checks)


def migrate(
    sqlite_path: Path,
    database_url: str,
    *,
    connection_factory: Callable[[str | None, str], Any] = connect,
    schema_initializer: Callable[[Any], None] = ensure_schema,
) -> MigrationReport:
    """Copy a KneeROM SQLite database into an empty PostgreSQL database."""

    if not database_url or not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("A PostgreSQL database URL is required")
    source_data = _read_source(Path(sqlite_path))
    destination = connection_factory(database_url, str(sqlite_path))
    try:
        if getattr(destination, "dialect", None) != "postgresql":
            raise ValueError("Destination connection is not PostgreSQL")
        schema_initializer(destination)
        _assert_empty(destination)
        destination.commit()
        try:
            _insert_rows(destination, source_data)
            _reset_identities(destination)
            _verify_foreign_keys(destination)
            destination_data = _fetch_destination(destination)
            report = _build_report(source_data, destination_data)
            destination.commit()
            return report
        except Exception:
            destination.rollback()
            raise
    finally:
        destination.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate KneeROM SQLite to PostgreSQL")
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument(
        "--database-url-file",
        type=Path,
        required=True,
        help="Path to a local file containing the PostgreSQL URL",
    )
    args = parser.parse_args()
    database_url = args.database_url_file.read_text(encoding="utf-8-sig").strip()
    report = migrate(args.sqlite_path, database_url)
    for table in ("patients", "doctors", "measurements"):
        verification = getattr(report, table)
        print(
            f"{table}: {verification.destination_count} rows, "
            f"sha256={verification.destination_digest}"
        )
    print(f"Migration verified: {report.total_rows} total rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
