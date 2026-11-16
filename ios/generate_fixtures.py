"""Generate synthetic Swift decoder fixtures using the existing desktop decoder.

Run from the repository root: python ios/generate_fixtures.py
No adapter is opened, no commands are sent, and no personal logs are used.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colorado_dashboard as desktop


def raw_reply(pid, data):
    payload = bytes((0x41, pid)) + data
    if len(payload) <= 7:
        frames = [bytes((len(payload),)) + payload]
    else:
        frames = [bytes((0x10, len(payload))) + payload[:6]]
        for index, start in enumerate(range(6, len(payload), 7), 1):
            frames.append(bytes((0x20 | index,)) + payload[start:start+7])
    return "\r".join("18DAF111" + frame.hex().upper() for frame in frames) + "\r>"


fixtures = []
for pid, spec in desktop.SENSORS.items():
    if pid >= 256:
        continue
    for value in (0, 1, 80, 128, 255):
        raw = raw_reply(pid, bytes((value,)) * spec[2])
        decoded, quality = desktop.decode(pid, raw)
        fixtures.append({"pid": pid, "raw": raw, "readings": [
            {"pid": pid, "value": decoded, "quality": quality}]})

for pid, data in ((0x67, bytes.fromhex("035064")), (0x67, bytes.fromhex("010050")),
                  (0x68, bytes.fromhex("3F005A646E7882")), (0x68, bytes(7)),
                  (0x87, bytes.fromhex("0319003200")), (0x87, bytes.fromhex("030000FFFF")),
                  (0x87, bytes.fromhex("0019003200"))):
    raw = raw_reply(pid, data)
    fixtures.append({"pid": pid, "raw": raw, "readings": [
        {"pid": channel, "value": value, "quality": quality}
        for channel, value, quality in desktop.decode_extended(pid, raw)]})

destination = Path(__file__).parent / "ColoradoCore/Tests/ColoradoCoreTests/desktop-fixtures.json"
destination.write_text(json.dumps(fixtures, indent=2) + "\n", encoding="utf-8")
print(f"Generated {len(fixtures)} synthetic desktop parity fixtures.")
