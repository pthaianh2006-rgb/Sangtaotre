import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
SCRIPTS_DIR = (Path(__file__).resolve().parents[1]/"scripts")
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
PROJECT_DIR = (Path(__file__).resolve().parents[1]/"aclproject2-main"/"aclproject2-main")
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import migrate_sqlite_to_postgres as migration


class CursorDouble:
    def __init__(self, cursor=None, rows=None):
        self.cursor = cursor
        self.rows = rows

    def fetchone(self):
        if self.rows is not None:
            return self.rows[0] if self.rows else None
        return self.cursor.fetchone()

    def fetchall(self):
        if self.rows is not None:
            return list(self.rows)
        return self.cursor.fetchall()


class PostgresDouble:
    dialect = "postgresql"

    def __init__(self):
        self.raw = sqlite3.connect(":memory:")
        self.raw.execute("PRAGMA foreign_keys=ON")
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.identity_tables = []
        self.fail_on_measurements = False

    def create_schema(self):
        self.raw.executescript(
            """
            CREATE TABLE patients(
                id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT,
                name TEXT, age INTEGER, week INTEGER, doctor_advice TEXT,
                rom_goal INTEGER, rom_goal_days INTEGER, rom_goal_start TEXT
            );
            CREATE TABLE doctors(
                id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, name TEXT
            );
            CREATE TABLE measurements(
                id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id),
                angle REAL, time TEXT, source TEXT
            );
            """
        )

    def execute(self, sql, parameters=None):
        normalized = " ".join(sql.split())
        if "SELECT setval(" in normalized:
            table = normalized.split("pg_get_serial_sequence('", 1)[1].split("'", 1)[0]
            self.identity_tables.append(table)
            return CursorDouble(rows=[(None,)])
        cursor = self.raw.execute(sql, parameters or ())
        return CursorDouble(cursor=cursor)

    def executemany(self, sql, rows):
        materialized = list(rows)
        if self.fail_on_measurements and "INSERT INTO measurements" in sql:
            raise RuntimeError("forced copy failure")
        self.raw.executemany(sql, materialized)
        return CursorDouble(rows=[])

    def commit(self):
        self.raw.commit()
        self.commits += 1

    def rollback(self):
        self.raw.rollback()
        self.rollbacks += 1

    def close(self):
        self.closed = True


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.source = Path(self.tempdir.name) / "legacy.db"
        db = sqlite3.connect(self.source)
        db.executescript(
            """
            CREATE TABLE users(
                id INTEGER PRIMARY KEY, username TEXT, password TEXT, name TEXT,
                age INTEGER, week INTEGER, role TEXT, doctor_advice TEXT,
                rom_goal INTEGER, rom_goal_days INTEGER, rom_goal_start TEXT
            );
            CREATE TABLE measurements(
                id INTEGER PRIMARY KEY, patient_id INTEGER, angle REAL,
                time TEXT, source TEXT
            );
            """
        )
        db.executemany(
            "INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                (3, "patient", "hash-p", "An", 29, 4, "patient", "Tập chậm", 110, 7, "2026-10-01"),
                (8, "doctor", "hash-d", "BS Bình", None, None, "doctor", "", None, None, None),
            ],
        )
        db.executemany(
            "INSERT INTO measurements VALUES(?,?,?,?,?)",
            [(5, 3, 91.25, "2026-10-20 09:00:00", "imu"), (9, 3, 95.0, "2026-10-21 09:00:00", "camera")],
        )
        db.commit()
        db.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def _migrate(self, destination):
        return migration.migrate(
            self.source,
            "postgresql://secret@localhost/kneerom",
            connection_factory=lambda _url, _path: destination,
            schema_initializer=lambda connection: connection.create_schema(),
        )

    def test_preserves_split_accounts_measurements_ids_and_digests(self):
        destination = PostgresDouble()
        report = self._migrate(destination)

        self.assertEqual((1, 1, 2), (
            report.patients.destination_count,
            report.doctors.destination_count,
            report.measurements.destination_count,
        ))
        self.assertEqual(
            (3, "patient", "hash-p", "An", 29, 4, "Tập chậm", 110, 7, "2026-10-01"),
            destination.raw.execute("SELECT * FROM patients").fetchone(),
        )
        self.assertEqual((8, "doctor", "hash-d", "BS Bình"), destination.raw.execute("SELECT * FROM doctors").fetchone())
        self.assertEqual([5, 9], [row[0] for row in destination.raw.execute("SELECT id FROM measurements ORDER BY id")])
        self.assertEqual(report.patients.source_digest, report.patients.destination_digest)
        self.assertEqual(["patients", "doctors", "measurements"], destination.identity_tables)
        self.assertTrue(destination.closed)

    def test_rejects_nonempty_destination(self):
        destination = PostgresDouble()
        original_create = destination.create_schema

        def occupied_schema():
            original_create()
            destination.raw.execute("INSERT INTO doctors VALUES(1,'existing','x','Existing')")
            destination.raw.commit()

        destination.create_schema = occupied_schema
        with self.assertRaisesRegex(ValueError, "not empty"):
            self._migrate(destination)

    def test_rolls_back_entire_copy_on_failure(self):
        destination = PostgresDouble()
        destination.fail_on_measurements = True
        with self.assertRaisesRegex(RuntimeError, "forced copy failure"):
            self._migrate(destination)
        self.assertEqual(0, destination.raw.execute("SELECT COUNT(*) FROM patients").fetchone()[0])
        self.assertEqual(0, destination.raw.execute("SELECT COUNT(*) FROM doctors").fetchone()[0])
        self.assertEqual(1, destination.rollbacks)

    def test_refuses_orphan_measurements_before_opening_destination(self):
        db = sqlite3.connect(self.source)
        db.execute("INSERT INTO measurements VALUES(10,999,50,'2026-10-22','imu')")
        db.commit()
        db.close()
        opened = []
        with self.assertRaisesRegex(ValueError, "non-patient"):
            migration.migrate(
                self.source,
                "postgresql://secret@localhost/kneerom",
                connection_factory=lambda *_args: opened.append(True),
            )
        self.assertEqual([], opened)

    def test_digest_is_deterministic_and_sensitive_to_data(self):
        rows = [(1, "á", 2.5, None)]
        self.assertEqual(migration.digest_rows(rows), migration.digest_rows(list(rows)))
        self.assertNotEqual(migration.digest_rows(rows), migration.digest_rows([(1, "á", 2.6, None)]))


if __name__ == "__main__":
    unittest.main()
