"""Exercise real Tk widgets on Windows CI (or a local X display), without opening a vehicle port."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import colorado_dashboard as dashboard
from colorado_log_viewer import load_recording


@unittest.skipUnless(sys.platform == "win32" or os.environ.get("DISPLAY"), "Requires a Tk desktop")
class DesktopUITests(unittest.TestCase):
    def test_layout_capabilities_playback_and_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            fixture_path = Path(__file__).parents[1] / "ios/ColoradoCore/Tests/ColoradoCoreTests/session-fixture.json"
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            (folder / "sensors.csv").write_text(fixture["csv"], encoding="utf-8")
            (folder / "session.json").write_text(json.dumps(fixture["metadata"]), encoding="utf-8")
            (folder / "events.jsonl").write_text(fixture["events"], encoding="utf-8")
            with patch.object(dashboard, "app_directory", return_value=folder), patch.object(dashboard, "logs_directory", return_value=folder):
                root = tk.Tk()
                root.withdraw()
                try:
                    app = dashboard.Dashboard(root)
                    root.update()
                    self.assertEqual(list(app.gauges), ["000D", "000C", "boost", "0005"])
                    app.layout["signals"] = ["boost", "000C"]
                    app.build_gauges()
                    self.assertEqual(list(app.gauges), ["boost", "000C"])
                    app.customize()
                    root.update()
                    dialogs = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
                    self.assertTrue(dialogs)
                    for dialog in dialogs:
                        dialog.destroy()
                    app.session_folder = folder
                    app.raw_log = io.StringIO()
                    app.handle({"type": "inventory", "name": "ECM", "supported": [5, 12, 135], "active": [5, 12, 34561, 34562], "profile": "Performance"})
                    self.assertEqual(app.capabilities.set("12", "support"), "Waiting")
                    self.assertEqual(app.capabilities.set("16", "support"), "Not advertised by ECM")
                    review = app.log_review
                    review.load(folder / "sensors.csv")
                    deadline = time.monotonic() + 10
                    while review.recording is None and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.01)
                    self.assertIsNotNone(review.recording)
                    review.cursor.set(2.5)
                    review.seek()
                    self.assertIn("Engine speed: —", review.snapshot.get())
                    review.cursor.set(4)
                    review.seek()
                    self.assertIn("2000.0 RPM", review.snapshot.get())
                    review.event_choice.current(0)
                    review.jump_event()
                    self.assertAlmostEqual(review.cursor.get(), 3.25)
                    review.edit_notes()
                    root.update()
                    self.assertTrue(any(isinstance(child, tk.Toplevel) for child in review.winfo_children()))
                    review.toggle_play()
                    self.assertTrue(review.playing)
                    review.play_clock = time.monotonic() - 2
                    review.play_tick()
                    self.assertFalse(review.playing)
                    self.assertEqual(review.cursor.get(), 4)
                finally:
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
