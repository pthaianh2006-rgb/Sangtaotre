"""Construct the real Tk widgets and plot with a hidden window, no device access."""
import unittest
from unittest.mock import patch

import knee_longer


class GuiRenderTests(unittest.TestCase):
    def test_hidden_window_builds_and_updates_plot(self):
        try:
            root = knee_longer.tk.Tk()
        except knee_longer.tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        app = None
        try:
            with patch.object(knee_longer.list_ports, "comports", return_value=[]), patch.object(knee_longer.serial, "Serial") as serial:
                app = knee_longer.KneeApp(root)
                app.conn_t0 = 10
                app.t0 = 10
                app._on_flex(20, 10.1, app._session_id)
                app._on_flex(100, 10.2, app._session_id)
                app._render_measurement()
                app._refresh_plot()
                root.update_idletasks()
                self.assertEqual(list(app.plot_line.get_ydata()), [20, 100])
                self.assertTrue(app.plot_fill.get_visible())
                self.assertEqual(app.rom_lbl.cget("text"), "100.0 °")
                serial.assert_not_called()
        finally:
            if app is not None:
                app._finish_close()
            else:
                root.destroy()


if __name__ == "__main__":
    unittest.main()