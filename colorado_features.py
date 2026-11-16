"""Offline dashboard preferences and portable recording annotations. No OBD I/O."""
import bisect
from datetime import datetime
import json
import math
from pathlib import Path
import uuid

PRESETS = {
    "Everyday": ["000D", "000C", "boost", "0005"],
    "Performance": ["000C", "boost", "0011", "000E"],
    "Towing": ["0005", "0004", "boost", "000F"],
    "Temperatures": ["0005", "000F", "6701", "6801"],
}
UNVERIFIED = ("Oil pressure", "Oil temperature", "Knock retard", "Transmission temperature",
              "Commanded / actual gear", "Torque-converter slip", "Requested boost", "Fuel-rail pressure")


def usable(value, quality):
    return value is not None and math.isfinite(value) and (
        quality == "Received / unvalidated" or quality.startswith("Derived from asynchronous"))


def gauge_catalog(sensors):
    ranges = {"RPM": (0, 6000), "mph": (0, 120), "°F": (0, 300), "kPa": (0, 400),
              "%": (0, 100), "V": (8, 18), "deg": (-30, 60), "g/s": (0, 300)}
    result = {f"{pid:04X}": (spec[0], spec[1], *ranges.get(spec[1], (0, 100)), pid)
              for pid, spec in sensors.items()}
    result["boost"] = ("Boost / vacuum", "psi", -15, 30, None)
    return result


def normalize_layout(value, catalog):
    if not isinstance(value, dict):
        value = {}
    signals = value.get("signals", PRESETS["Everyday"])
    if not isinstance(signals, list):
        signals = PRESETS["Everyday"]
    signals = list(dict.fromkeys(s for s in signals if isinstance(s, str) and s in catalog))[:4]
    return {"schema_version": 1, "signals": signals or PRESETS["Everyday"],
            "large_cards": value.get("large_cards") is True}


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def append_marker(folder, label, note="", timestamp=None):
    label, note = label.strip()[:120], note.strip()[:2000]
    if not label:
        raise ValueError("Enter an event label.")
    event = {"schema_version": 1, "id": str(uuid.uuid4()), "timestamp": timestamp or datetime.now().astimezone().isoformat(),
             "label": label, "note": note}
    stamp = datetime.fromisoformat(event["timestamp"])
    if stamp.tzinfo is None:
        raise ValueError("Event timestamp requires a timezone")
    with (Path(folder) / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def load_markers(folder):
    events, bad = [], 0
    path = Path(folder) / "events.jsonl"
    if not path.exists():
        return events, bad
    with path.open(encoding="utf-8-sig") as handle:
        for index, line in enumerate(handle):
            if index >= 10000:
                bad += 1
                break
            try:
                event = json.loads(line)
                stamp = datetime.fromisoformat(event["timestamp"])
                if stamp.tzinfo is None or not isinstance(event["label"], str) or not event["label"].strip():
                    raise ValueError("Invalid marker")
                events.append({**event, "time": stamp.timestamp()})
            except (ValueError, TypeError, KeyError):
                bad += 1
    return sorted(events, key=lambda e: e["time"]), bad


def sample_at(samples, timestamp, max_age=3):
    """Last actual sample at/before the cursor. Never look ahead or bridge invalid data."""
    index = bisect.bisect_right(samples, timestamp, key=lambda sample: sample[0]) - 1
    if index < 0:
        return None
    sample = samples[index]
    return sample if timestamp - sample[0] <= max_age and usable(sample[1], sample[2]) else None
