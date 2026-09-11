import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import knee_core
import knee_longer
from tien_ich import knee_longer_cli as cli

class UsbConnectionTests(unittest.TestCase):
    def app(self):
        root = Mock()
        root.winfo_fpixels.return_value = 96
        with patch.object(knee_longer.KneeApp, "_build_ui"):
            app = knee_longer.KneeApp(root)
        app.port_var = Mock()
        app.port_var.get.return_value = ""
        app.port_cb = MagicMock()
        app._log = Mock()
        return app

    def ports(self):
        return [SimpleNamespace(device="COM3", description="Intel AMT SOL", vid=None, hwid="PCI"),
                SimpleNamespace(device="COM8", description="USB-SERIAL CH340", vid=0x1a86, hwid="USB")]

    def test_usb_is_suggested_before_intel_amt(self):
        app=self.app()
        with patch.object(knee_longer.list_ports,"comports",return_value=self.ports()):
            app._scan_ports()
        self.assertEqual(next(iter(app._port_map.values())), "COM8")
        app.port_cb.current.assert_called_once_with(0)

    def test_refresh_preserves_explicit_port_selection(self):
        app=self.app()
        app.port_var.get.return_value="COM3  (Intel AMT SOL)"
        with patch.object(knee_longer.list_ports,"comports",return_value=self.ports()):
            app._scan_ports()
        app.port_cb.current.assert_not_called()

    def test_missing_device_never_opens_fabricated_com6(self):
        app=self.app()
        app._port_map={}
        with patch.object(knee_longer.list_ports,"comports",return_value=[]), patch.object(knee_longer.serial,"Serial") as serial, patch.object(knee_longer.messagebox,"showerror") as error:
            app._connect()
        serial.assert_not_called()
        error.assert_called_once()
        self.assertEqual(app._port_map,{})

    def test_reader_closes_before_publishing_disconnect(self):
        app=self.app()
        connection=Mock()
        connection.readline.side_effect=knee_longer.serial.SerialException("unplugged")
        messages=Mock()
        def assert_released(item, **kwargs):
            connection.close.assert_called_once()
            self.assertEqual(item,("disconnected","unplugged"))
        messages.put.side_effect=assert_released
        app._reader_loop(connection,threading.Event(),messages)
        messages.put.assert_called_once()

    def test_normal_reader_shutdown_releases_port(self):
        app=self.app()
        connection=Mock()
        stop=threading.Event()
        stop.set()
        app._reader_loop(connection,stop,queue.Queue())
        connection.close.assert_called_once()
        connection.readline.assert_not_called()

    def test_failed_thread_start_releases_port(self):
        app=self.app()
        app.port_var.get.return_value="USB"
        app._port_map={"USB":"COM8"}
        connection=Mock()
        with patch.object(knee_longer.serial,"Serial",return_value=connection), patch.object(knee_longer.threading,"Thread") as worker, patch.object(knee_longer.messagebox,"showerror"):
            worker.return_value.start.side_effect=RuntimeError("no thread")
            app._connect()
        connection.close.assert_called_once()
        self.assertIsNone(app.ser)
        self.assertIsNone(app.reader_thread)

    def test_calibration_prompt_is_logged_once(self):
        app=self.app()
        connection=Mock()
        connection.readline.side_effect=[b"... send C\n", b"... send C\n", knee_longer.serial.SerialException("unplugged")]
        messages=queue.Queue()
        app._reader_loop(connection,threading.Event(),messages)
        events=list(messages.queue)
        self.assertEqual([kind for kind,_ in events],["log","disconnected"])
        self.assertIn("chờ lệnh C", events[0][1])
        connection.close.assert_called_once()

    def test_access_error_keeps_original_message(self):
        error=knee_longer.serial.SerialException("PermissionError(13, 'Access is denied.', None, 5)")
        message=knee_core.serial_connection_error("COM8",error)
        self.assertIn(str(error),message)
        self.assertIn("COM8",message)
        self.assertIn("Serial Monitor",message)

    def test_cli_default_is_usb(self):
        with patch.object(cli.list_ports,"comports",return_value=self.ports()), patch("builtins.input",return_value=""), patch("builtins.print"):
            self.assertEqual(cli.chon_cong(),"COM8")

if __name__=="__main__":
    unittest.main()