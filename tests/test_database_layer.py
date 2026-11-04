import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = (Path(__file__).resolve().parents[1]/"aclproject2-main"/"aclproject2-main")
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import database_layer


class DatabaseLayerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "adapter.db")
        self.conn = database_layer.connect(None, self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def test_sqlite_execute_returns_tuple_rows_and_persists_commit(self):
        self.conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("INSERT INTO sample(name) VALUES (?)", ("Lan",))
        self.conn.commit()

        row = self.conn.execute(
            "SELECT id, name FROM sample WHERE name = ?", ("Lan",)
        ).fetchone()
        self.assertEqual((1, "Lan"), row)
        self.assertIsInstance(row, tuple)

        raw = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(1, raw.execute("SELECT COUNT(*) FROM sample").fetchone()[0])
        finally:
            raw.close()

    def test_sqlite_cursor_executemany_and_rollback(self):
        cursor = self.conn.cursor()
        cursor.execute("CREATE TABLE sample (value INTEGER)")
        cursor.executemany("INSERT INTO sample(value) VALUES (?)", [(1,), (2,)])
        self.conn.rollback()

        count = self.conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
        self.assertEqual(0, count)

    def test_integrity_error_tuple_catches_sqlite_constraint_error(self):
        self.conn.execute("CREATE TABLE sample (name TEXT UNIQUE)")
        self.conn.execute("INSERT INTO sample(name) VALUES (?)", ("same",))
        with self.assertRaises(database_layer.IntegrityError):
            self.conn.execute("INSERT INTO sample(name) VALUES (?)", ("same",))

    def test_placeholder_conversion_ignores_quoted_literals_and_comments(self):
        sql = (
            "SELECT '?', \"?\", value FROM sample "
            "WHERE a = ? AND note = 'it''s ?' -- ?\n"
            "AND b = ? /* ? */"
        )
        converted = database_layer.convert_placeholders(sql)
        self.assertEqual(
            "SELECT '?', \"?\", value FROM sample "
            "WHERE a = %s AND note = 'it''s ?' -- ?\n"
            "AND b = %s /* ? */",
            converted,
        )

    def test_postgresql_configuration_does_not_fall_back_to_sqlite(self):
        class FakePostgresError(Exception):
            pass

        class FakeIntegrityError(FakePostgresError):
            pass

        class FakePsycopg:
            Error = FakePostgresError
            IntegrityError = FakeIntegrityError

            @staticmethod
            def connect(_database_url):
                raise FakePostgresError("connection failed")

        unused_sqlite = str(Path(self.tempdir.name) / "must-not-exist.db")
        with patch.object(database_layer.importlib, "import_module", return_value=FakePsycopg):
            with self.assertRaises(database_layer.DatabaseError):
                database_layer.connect(
                    "postgresql://invalid:invalid@127.0.0.1:1/missing", unused_sqlite
                )
        self.assertFalse(Path(unused_sqlite).exists())


    def test_schema_separates_patients_doctors_and_measurement_identity(self):
        database_layer.ensure_schema(self.conn)
        database_layer.ensure_schema(self.conn)

        tables = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertTrue({"patients", "doctors", "measurements"} <= tables)

        self.conn.execute(
            "INSERT INTO patients(username,password,name,age,week) VALUES(?,?,?,?,?)",
            ("shared", "patient-hash", "Patient", 30, 2),
        )
        self.conn.execute(
            "INSERT INTO doctors(username,password,name) VALUES(?,?,?)",
            ("shared", "doctor-hash", "Doctor"),
        )
        patient_id = self.conn.execute(
            "SELECT id FROM patients WHERE username=?", ("shared",)
        ).fetchone()[0]
        measurement_id = database_layer.insert_measurement(
            self.conn, patient_id, 92.5, "2026-10-22 10:00:00", "imu"
        )
        self.conn.commit()

        self.assertEqual(1, measurement_id)
        self.assertEqual(
            (patient_id, 92.5, "imu"),
            self.conn.execute(
                "SELECT patient_id,angle,source FROM measurements WHERE id=?",
                (measurement_id,),
            ).fetchone(),
        )
        fk_targets = {row[2] for row in self.conn.execute(
            "PRAGMA foreign_key_list(measurements)"
        ).fetchall()}
        self.assertEqual({"patients"}, fk_targets)



if __name__ == "__main__":
    unittest.main()
