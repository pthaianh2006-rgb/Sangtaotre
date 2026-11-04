"""Small DB-API compatibility layer for SQLite and PostgreSQL.

SQLite remains the default when no database URL is configured. PostgreSQL is
selected explicitly with ACL_DATABASE_URL and never falls back to SQLite.
"""

from __future__ import annotations

import importlib
import re
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any


class PostgresDatabaseError(Exception):
    """Normalized PostgreSQL database failure."""


class PostgresIntegrityError(PostgresDatabaseError):
    """Normalized PostgreSQL constraint failure."""


DatabaseError = (sqlite3.Error, PostgresDatabaseError)
IntegrityError = (sqlite3.IntegrityError, PostgresIntegrityError)

_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def convert_placeholders(sql: str) -> str:
    """Convert DB-API qmark parameters to psycopg format outside SQL literals."""

    output: list[str] = []
    index = 0
    state = "normal"
    dollar_tag = ""

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if state == "normal":
            if char == "'":
                state = "single"
                output.append(char)
            elif char == '"':
                state = "double"
                output.append(char)
            elif char == "-" and next_char == "-":
                state = "line_comment"
                output.extend((char, next_char))
                index += 1
            elif char == "/" and next_char == "*":
                state = "block_comment"
                output.extend((char, next_char))
                index += 1
            elif char == "$":
                match = _DOLLAR_TAG.match(sql, index)
                if match:
                    dollar_tag = match.group(0)
                    state = "dollar"
                    output.append(dollar_tag)
                    index = match.end() - 1
                else:
                    output.append(char)
            elif char == "?":
                output.append("%s")
            else:
                output.append(char)

        elif state == "single":
            output.append(char)
            if char == "'":
                if next_char == "'":
                    output.append(next_char)
                    index += 1
                else:
                    state = "normal"

        elif state == "double":
            output.append(char)
            if char == '"':
                if next_char == '"':
                    output.append(next_char)
                    index += 1
                else:
                    state = "normal"

        elif state == "line_comment":
            output.append(char)
            if char in "\r\n":
                state = "normal"

        elif state == "block_comment":
            output.append(char)
            if char == "*" and next_char == "/":
                output.append(next_char)
                index += 1
                state = "normal"

        else:  # dollar-quoted PostgreSQL literal
            if sql.startswith(dollar_tag, index):
                output.append(dollar_tag)
                index += len(dollar_tag) - 1
                state = "normal"
            else:
                output.append(char)

        index += 1

    return "".join(output)


def _postgres_error(error: Exception, psycopg: Any) -> PostgresDatabaseError:
    if isinstance(error, psycopg.IntegrityError):
        return PostgresIntegrityError(str(error))
    return PostgresDatabaseError(str(error))


class CursorAdapter:
    def __init__(self, cursor: Any, dialect: str, psycopg: Any | None = None):
        self._cursor = cursor
        self.dialect = dialect
        self._psycopg = psycopg

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> Any:
        return getattr(self._cursor, "lastrowid", None)

    def execute(self, sql: str, parameters: Sequence[Any] | None = None) -> "CursorAdapter":
        statement = convert_placeholders(sql) if self.dialect == "postgresql" else sql
        try:
            if parameters is None:
                self._cursor.execute(statement)
            else:
                self._cursor.execute(statement, parameters)
        except Exception as error:
            if self._psycopg is not None and isinstance(error, self._psycopg.Error):
                raise _postgres_error(error, self._psycopg) from error
            raise
        return self

    def executemany(self, sql: str, parameters: Iterable[Sequence[Any]]) -> "CursorAdapter":
        statement = convert_placeholders(sql) if self.dialect == "postgresql" else sql
        try:
            self._cursor.executemany(statement, parameters)
        except Exception as error:
            if self._psycopg is not None and isinstance(error, self._psycopg.Error):
                raise _postgres_error(error, self._psycopg) from error
            raise
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()

    def __iter__(self):
        return iter(self._cursor)


class ConnectionAdapter:
    def __init__(self, connection: Any, dialect: str, psycopg: Any | None = None):
        self._connection = connection
        self.dialect = dialect
        self._psycopg = psycopg

    def cursor(self) -> CursorAdapter:
        return CursorAdapter(self._connection.cursor(), self.dialect, self._psycopg)

    def execute(self, sql: str, parameters: Sequence[Any] | None = None) -> CursorAdapter:
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, parameters: Iterable[Sequence[Any]]) -> CursorAdapter:
        return self.cursor().executemany(sql, parameters)

    def _call(self, method: str) -> None:
        try:
            getattr(self._connection, method)()
        except Exception as error:
            if self._psycopg is not None and isinstance(error, self._psycopg.Error):
                raise _postgres_error(error, self._psycopg) from error
            raise

    def commit(self) -> None:
        self._call("commit")

    def rollback(self) -> None:
        self._call("rollback")

    def close(self) -> None:
        self._call("close")

    def __enter__(self) -> "ConnectionAdapter":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def connect(database_url: str | None, sqlite_path: str) -> ConnectionAdapter:
    if database_url:
        try:
            psycopg = importlib.import_module("psycopg")
        except (ImportError, ModuleNotFoundError) as error:
            raise PostgresDatabaseError(
                "PostgreSQL is configured but psycopg is not installed"
            ) from error

        try:
            connection = psycopg.connect(database_url)
        except psycopg.Error as error:
            raise _postgres_error(error, psycopg) from error
        return ConnectionAdapter(connection, "postgresql", psycopg)

    connection = sqlite3.connect(sqlite_path, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return ConnectionAdapter(connection, "sqlite")


def _sqlite_table_exists(connection: ConnectionAdapter, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _ensure_sqlite_schema(connection: ConnectionAdapter) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            age INTEGER,
            week INTEGER,
            doctor_advice TEXT DEFAULT '',
            rom_goal INTEGER,
            rom_goal_days INTEGER,
            rom_goal_start TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL
        )
        """
    )

    # Split the legacy account table without deleting it. Keeping the source
    # table makes rollback and migration auditing possible.
    if _sqlite_table_exists(connection, "users"):
        legacy_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        optional = {
            "doctor_advice": "''",
            "rom_goal": "NULL",
            "rom_goal_days": "NULL",
            "rom_goal_start": "NULL",
        }
        selected = [
            column if column in legacy_columns else fallback
            for column, fallback in optional.items()
        ]
        connection.execute(
            """
            INSERT OR IGNORE INTO patients(
                id, username, password, name, age, week,
                doctor_advice, rom_goal, rom_goal_days, rom_goal_start
            )
            SELECT id, username, password, name, age, week, %s, %s, %s, %s
            FROM users WHERE role='patient'
            """
            % tuple(selected)
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO doctors(id, username, password, name)
            SELECT id, username, password, name FROM users WHERE role='doctor'
            """
        )

    if not _sqlite_table_exists(connection, "measurements"):
        connection.execute(
            """
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                angle REAL NOT NULL,
                time TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'camera',
                FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE
            )
            """
        )
    else:
        measurement_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(measurements)").fetchall()
        }
        if "source" not in measurement_columns:
            connection.execute(
                "ALTER TABLE measurements ADD COLUMN source TEXT DEFAULT 'camera'"
            )
        foreign_targets = {
            row[2]
            for row in connection.execute(
                "PRAGMA foreign_key_list(measurements)"
            ).fetchall()
        }
        if foreign_targets != {"patients"}:
            connection.execute("DROP TABLE IF EXISTS measurements_split")
            connection.execute(
                """
                CREATE TABLE measurements_split (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER NOT NULL,
                    angle REAL NOT NULL,
                    time TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'camera',
                    FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO measurements_split(id, patient_id, angle, time, source)
                SELECT id, patient_id, angle, time, COALESCE(source, 'camera')
                FROM measurements
                """
            )
            connection.execute("DROP TABLE measurements")
            connection.execute("ALTER TABLE measurements_split RENAME TO measurements")

    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_measurements_patient_id_id "
        "ON measurements(patient_id, id DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_measurements_patient_source_time "
        "ON measurements(patient_id, source, time)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS care_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            image_name TEXT NOT NULL,
            note TEXT DEFAULT '',
            pain_level INTEGER,
            limb_side TEXT NOT NULL DEFAULT 'right',
            created_at TEXT NOT NULL,
            doctor_reply TEXT DEFAULT '',
            doctor_reply_at TEXT,
            doctor_id INTEGER,
            FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
            FOREIGN KEY(doctor_id) REFERENCES doctors(id) ON DELETE SET NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_care_updates_patient_id_id "
        "ON care_updates(patient_id, id DESC)"
    )
    connection.commit()


def _ensure_postgresql_schema(connection: ConnectionAdapter) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS patients (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            age INTEGER,
            week INTEGER,
            doctor_advice TEXT DEFAULT '',
            rom_goal INTEGER,
            rom_goal_days INTEGER,
            rom_goal_start TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS doctors (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS measurements (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            patient_id BIGINT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
            angle DOUBLE PRECISION NOT NULL,
            time TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'camera'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_measurements_patient_id_id "
        "ON measurements(patient_id, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_measurements_patient_source_time "
        "ON measurements(patient_id, source, time)",
        """
        CREATE TABLE IF NOT EXISTS care_updates (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            patient_id BIGINT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
            image_name TEXT NOT NULL,
            note TEXT DEFAULT '',
            pain_level INTEGER,
            limb_side TEXT NOT NULL DEFAULT 'right',
            created_at TEXT NOT NULL,
            doctor_reply TEXT DEFAULT '',
            doctor_reply_at TEXT,
            doctor_id BIGINT REFERENCES doctors(id) ON DELETE SET NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_care_updates_patient_id_id "
        "ON care_updates(patient_id, id DESC)",
    )
    for statement in statements:
        connection.execute(statement)
    connection.commit()


def ensure_schema(connection: ConnectionAdapter) -> None:
    """Create the split patient/doctor schema for the selected database."""

    if connection.dialect == "postgresql":
        _ensure_postgresql_schema(connection)
    else:
        _ensure_sqlite_schema(connection)


def insert_measurement(
    connection: ConnectionAdapter,
    patient_id: int,
    angle: float,
    measured_at: str,
    source: str,
) -> int:
    """Insert a measurement and return its generated identifier."""

    parameters = (patient_id, angle, measured_at, source)
    if connection.dialect == "postgresql":
        cursor = connection.execute(
            """
            INSERT INTO measurements(patient_id, angle, time, source)
            VALUES (?, ?, ?, ?) RETURNING id
            """,
            parameters,
        )
        row = cursor.fetchone()
        if row is None:
            raise PostgresDatabaseError("PostgreSQL did not return a measurement id")
        return int(row[0])

    cursor = connection.execute(
        """
        INSERT INTO measurements(patient_id, angle, time, source)
        VALUES (?, ?, ?, ?)
        """,
        parameters,
    )
    return int(cursor.lastrowid)
