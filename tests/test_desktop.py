import contextlib
import importlib
import io
import itertools
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest import mock
from openpyxl import load_workbook
import knee_core
import knee_longer
from tien_ich import knee_longer_cli as cli


class SerialParsingTests(unittest.TestCase):
    def test_supported_formats_keep_angle_convention(self):
        for line, expected in (("Knee: 180", 0), ("Knee: 90.5 deg", 89.5), ("1234,120.5", 59.5), ("Knee: 200", -20), ("Knee: 0", 180)):
            with self.subTest(line=line):
                self.assertEqual(knee_core.parse_knee_line(line), expected)

    def test_invalid_frames_are_rejected(self):
        for line in ("", "[ALIGN] 10,90", "Knee:", "Knee: nan", "Knee: inf", "Knee: -1", "Knee: 201", "1,nan", "1,inf", "1,2,3", "text"):
            with self.subTest(line=line):
                self.assertIsNone(knee_core.parse_knee_line(line))

    def test_partial_reads_survive_timeouts(self):
        frames = knee_core.SerialLines()
        for chunk in (b"Knee: 1", b"", b"80\r"):
            self.assertEqual(frames.feed(chunk), [])
        self.assertEqual(frames.feed(b"\n12,90\nKnee: "), ["Knee: 180", "12,90"])
        self.assertEqual(frames.feed(b"120\n"), ["Knee: 120"])

    def test_oversized_frame_discard_and_recovery(self):
        frames = knee_core.SerialLines(max_bytes=16)
        self.assertEqual(frames.feed(b"x" * 100), [])
        self.assertLessEqual(len(frames.pending), 16)
        self.assertEqual(frames.feed(b"garbage\nKnee: 90\n"), ["Knee: 90"])


class ExportTests(unittest.TestCase):
    def test_single_zero_sample_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "single.xlsx"
            ok, message = knee_core.export_excel([(1, 0, 0)], path)
            self.assertTrue(ok, message)
            workbook = load_workbook(path)
            try:
                self.assertEqual(list(workbook["Du lieu"].values)[1], (1, 0, 0, 90))
                for cell in ("B4", "B8", "B9", "B10"):
                    self.assertTrue(workbook["Thong ke"][cell].value.startswith("=IF("))
                self.assertEqual(len(workbook["Du lieu"]._charts), 1)
            finally:
                workbook.close()

    def test_failed_replace_preserves_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.xlsx"
            path.write_bytes(b"previous")
            with mock.patch.object(knee_core.os, "replace", side_effect=PermissionError("Excel holds file")):
                ok, _ = knee_core.export_excel([(1, 0, 90)], path)
            self.assertFalse(ok)
            self.assertEqual(path.read_bytes(), b"previous")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_save_preserves_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.xlsx"
            path.write_bytes(b"previous")
            workbook = mock.Mock()
            workbook.save.side_effect = OSError("disk full")
            with mock.patch.object(knee_core, "_build_workbook", return_value=workbook):
                ok, _ = knee_core.export_excel([(1, 0, 90)], path)
            self.assertFalse(ok)
            self.assertEqual(path.read_bytes(), b"previous")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_data_cannot_replace_report(self):
        datasets = [[(1, float("nan"), 90)], [(1, 0, float("inf"))], [(1, -1, 90)], [(1, 0, 181)], [(1, True, 90)], [(1, 1, 90), (2, 0, 80)], []]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.xlsx"
            path.write_bytes(b"original")
            for rows in datasets:
                with self.subTest(rows=rows):
                    self.assertFalse(knee_core.export_excel(rows, path)[0])
                    self.assertEqual(path.read_bytes(), b"original")


class DesktopTests(unittest.TestCase):
    def make_app(self):
        root = mock.MagicMock()
        root.winfo_fpixels.return_value = 96
        with mock.patch.object(knee_longer.KneeApp, "_build_ui"):
            app = knee_longer.KneeApp(root)
        for name in ("btn_conn", "hdr_status", "btn_calib", "btn_rec", "port_cb", "angle_lbl", "pill", "hz_lbl", "count_lbl", "rom_lbl", "pct_lbl", "accent_bar", "btn_export", "btn_new"):
            setattr(app, name, mock.Mock())
        app._log = mock.Mock()
        return app

    def test_init_runtime_state_without_hardware(self):
        with mock.patch.object(knee_longer.serial, "Serial") as serial:
            app = self.make_app()
        serial.assert_not_called()
        self.assertIsNone(app.t0)
        self.assertIsNone(app._rom)
        self.assertFalse(app._closing)
        self.assertFalse(app._exporting)
        self.assertEqual(app.msg_q.maxsize, 4096)
        self.assertIsInstance(app.export_q, queue.Queue)
        self.assertIsNotNone(app._pump_id)
        self.assertIsNotNone(app._plot_id)

    def test_queued_samples_keep_capture_time_and_membership(self):
        app = self.make_app()
        app.conn_t0, app.t0 = 5, 10
        app.recording = False
        session = app._session_id
        app._on_flex(30, 10.25, session)
        app._on_flex(90, 10.75, session)
        app._on_flex(120, 11, None)
        self.assertEqual(app.rows, [(1, .25, 30), (2, .75, 90)])
        self.assertEqual(app._rom, 90)
        app._session_id += 1
        app._on_flex(130, 11.5, session)
        self.assertEqual(len(app.rows), 2)

    def test_failed_record_command_preserves_state(self):
        app = self.make_app()
        with mock.patch.object(app, "_write", return_value=False):
            app.toggle_record()
        self.assertFalse(app.recording)
        self.assertIsNone(app.t0)

    def test_reader_reports_disconnect(self):
        app = self.make_app()
        app.recording = True
        connection = mock.Mock()
        connection.readline.side_effect = [b"Knee: 180\n", knee_longer.serial.SerialException("removed")]
        messages = queue.Queue(maxsize=4)
        with mock.patch.object(knee_longer.time, "monotonic", return_value=12):
            app._reader_loop(connection, threading.Event(), messages)
        self.assertEqual(messages.get_nowait(), ("flex", (0, 12, app._session_id)))
        self.assertEqual(messages.get_nowait(), ("disconnected", "removed"))

    def test_reconnect_gets_new_queue_and_event(self):
        app = self.make_app()
        app.port_var = mock.Mock()
        app.port_var.get.return_value = "Mock port"
        app._port_map = {"Mock port": "COM_TEST"}
        old_queue, old_stop = app.msg_q, app.stop_flag
        app.reader_thread = mock.Mock()
        app.reader_thread.is_alive.return_value = False
        connection = mock.Mock()
        with mock.patch.object(knee_longer.serial, "Serial", return_value=connection), mock.patch.object(knee_longer.threading, "Thread") as worker:
            app._connect()
        self.assertIsNot(app.msg_q, old_queue)
        self.assertIsNot(app.stop_flag, old_stop)
        self.assertEqual(worker.call_args.kwargs["args"], (connection, app.stop_flag, app.msg_q))
        worker.return_value.start.assert_called_once()


class CliTests(unittest.TestCase):
    def test_import_does_not_open_device_or_prompt(self):
        with mock.patch.object(cli.serial, "Serial") as serial, mock.patch("builtins.input") as prompt:
            importlib.reload(cli)
        serial.assert_not_called()
        prompt.assert_not_called()

    def test_help_does_not_open_device(self):
        with mock.patch.object(cli.serial, "Serial") as serial, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        serial.assert_not_called()

    def test_pause_resume_keeps_samples(self):
        connection = mock.Mock()
        connection.is_open = True
        connection.readline.side_effect = [b"Knee: 180\n", b"Knee: 90\n", b"Knee: 80\n"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.xlsx"
            with mock.patch.object(cli.serial, "Serial", return_value=connection), mock.patch.object(cli, "Keyboard") as keyboard, mock.patch.object(cli.time, "monotonic", side_effect=itertools.count(10, .1)), mock.patch.object(cli, "export_excel", return_value=(True, "saved")) as export, contextlib.redirect_stdout(io.StringIO()):
                keyboard.return_value.__enter__.return_value.read.side_effect = ["L", "L", "L", "Q"]
                status = cli.main(["--port", "COM_TEST", "--output", str(path)])
            self.assertEqual(status, 0)
            rows = export.call_args.args[0]
            self.assertEqual([row[2] for row in rows], [0, 100])
            self.assertGreater(rows[1][1], rows[0][1])
            connection.close.assert_called_once()

    def test_overwrite_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.xlsx"
            path.write_bytes(b"original")
            with mock.patch.object(cli.serial, "Serial") as serial, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main(["--port", "COM_TEST", "--output", str(path)])
            serial.assert_not_called()
            self.assertEqual(path.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()