import json
import tempfile
import unittest
from pathlib import Path

from colorado_features import (PRESETS, append_marker, gauge_catalog, load_markers,
                               normalize_layout, read_json, sample_at, write_json)
from colorado_dashboard import SENSORS
from colorado_log_viewer import load_recording


class RecordingTests(unittest.TestCase):
    def test_playback_does_not_look_ahead_or_bridge_invalid_or_stale_samples(self):
        good = "Received / unvalidated"
        samples = [(1, 10, good), (2, None, "No data"), (5, 50, good)]
        self.assertIsNone(sample_at(samples, .9))
        self.assertEqual(sample_at(samples, 1.9)[1], 10)
        self.assertIsNone(sample_at(samples, 2.1))
        self.assertIsNone(sample_at(samples, 4.9))
        self.assertEqual(sample_at(samples, 5)[1], 50)
        self.assertIsNone(sample_at(samples, 8.1))

    def test_annotations_preserve_metadata_and_round_trip_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            write_json(folder / "session.json", {"capabilities": {"supported": [12]}, "notes": "Before"})
            metadata = read_json(folder / "session.json")
            metadata.update(name="Tow test", notes="After, with \"quotes\"\nSecond line")
            write_json(folder / "session.json", metadata)
            self.assertEqual(read_json(folder / "session.json")["capabilities"]["supported"], [12])
            event = append_marker(folder, "Shift ✓", "Observed, not diagnosed", "2026-01-01T00:00:03.250+00:00")
            with (folder / "events.jsonl").open("a") as handle:
                handle.write('not json\n{"timestamp":"bad","label":"x"}\n')
            markers, bad = load_markers(folder)
            self.assertEqual(markers[0]["id"], event["id"])
            self.assertEqual(markers[0]["label"], "Shift ✓")
            self.assertEqual(bad, 2)
            with self.assertRaises(ValueError):
                append_marker(folder, " ")
            with self.assertRaises(ValueError):
                append_marker(folder, "Event", timestamp="2026-01-01T00:00:00")

    def test_layouts_contain_known_unique_signals_and_presets_do_not_enable_polling(self):
        catalog = gauge_catalog(SENSORS)
        for preset in PRESETS.values():
            self.assertTrue(set(preset).issubset(catalog))
        self.assertEqual(normalize_layout({"signals": ["boost", "boost", "unknown", 4]}, catalog)["signals"], ["boost"])
        self.assertEqual(normalize_layout({"signals": None}, catalog)["signals"], PRESETS["Everyday"])
        self.assertEqual(normalize_layout([], catalog)["signals"], PRESETS["Everyday"])

    def test_same_portable_session_fixture_as_swift(self):
        path = Path(__file__).parents[1] / "ios/ColoradoCore/Tests/ColoradoCoreTests/session-fixture.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "sensors.csv").write_text(fixture["csv"], encoding="utf-8")
            write_json(folder / "session.json", fixture["metadata"])
            (folder / "events.jsonl").write_text(fixture["events"], encoding="utf-8")
            rec = load_recording(folder)
            self.assertEqual(len(rec["series"]), 3)
            self.assertEqual(rec["bad_rows"], 2)
            self.assertEqual(rec["bad_events"], 1)
            self.assertEqual(rec["end"]-rec["start"], 4)
            self.assertEqual(rec["metadata"]["name"], "Portable ✓ session")
            rpm = next(ss for key, ss in rec["series"].items() if key[0] == "Engine speed")
            self.assertIsNone(sample_at(rpm, rec["start"]+2.5))
            self.assertEqual(rec["events"][0]["note"], 'Passenger marker, with "quotes"')

    def test_duplicate_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sensors.csv"
            path.write_text("timestamp,sensor,value,units,quality,value\n")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_recording(path)


if __name__ == "__main__":
    unittest.main()
