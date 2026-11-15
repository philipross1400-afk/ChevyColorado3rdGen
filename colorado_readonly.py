"""Finite, allowlisted OBDLink checks. No ECU writes, resets, or code clearing."""
import argparse
from datetime import datetime
import json
import multiprocessing as mp
from pathlib import Path
import queue
import re
import time

ECUS = {"11": "ECM-EngineControl", "18": "TCM-TransmisCtrl",
        "28": "BSCM-BrakeSystem", "40": "BCM-BodyControl", "45": "GWM-Gateway"}
ADAPTER = ("ATI", "ATDP", "ATDPN")
# Formatting, functional OBD addressing, and default receive filter are
# adapter settings only. These do not program vehicle modules.
VEHICLE = ("ATH1", "ATS1", "ATCAF1", "ATCFC1", "ATSH18DA11F1", "ATCRA18DAF111", "090A", "010C")
ALLOWED = frozenset(ADAPTER + VEHICLE)


def frames(raw):
    """Extract 29-bit response headers while retaining raw bytes in the log."""
    result = []
    for line in raw.splitlines():
        compact = re.sub(r"\s+", "", line).upper().strip(">")
        if re.fullmatch(r"18DAF1[0-9A-F]{2}(?:[0-9A-F]{2})+", compact):
            source = compact[6:8]
            result.append({"header": compact[:8], "source": source,
                           "module": ECUS.get(source, "Unmapped ECU"),
                           "data_hex": compact[8:]})
    return result


def exchange(port, command):
    if command not in ALLOWED:
        raise ValueError("Command is outside the read-only allowlist")
    port.write((command + "\r").encode("ascii"))
    port.flush()
    reply = bytearray()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        chunk = port.read(1)
        reply.extend(chunk)
        if b">" in chunk:
            return reply.decode("ascii", errors="replace")
        if len(reply) > 32768:
            raise RuntimeError("Unexpectedly large reply; stopping")
    raise TimeoutError("No complete adapter prompt within 8 seconds; stopping")


def worker(port_name, vehicle, events):
    try:
        import serial
        events.put({"status": "opening", "port": port_name})
        with serial.Serial(port_name, 115200, timeout=0.25, write_timeout=3) as port:
            events.put({"status": "opened"})
            protocol = None
            for command in ADAPTER + (VEHICLE if vehicle else ()):
                events.put({"status": "query", "command": command})
                raw = exchange(port, command)
                events.put({"status": "reply", "command": command,
                            "raw": raw, "frames": frames(raw)})
                if command in VEHICLE and command.startswith("AT"):
                    tokens = raw.replace(">", "").split()
                    if "OK" not in tokens:
                        raise RuntimeError("Adapter setup was not acknowledged; stopping before vehicle queries")
                if command == "ATDPN":
                    lines = [x.strip().upper() for x in raw.replace(">", "").splitlines()]
                    protocol = next((x for x in lines if x in ("7", "A7")), None)
                    if vehicle and protocol is None:
                        raise RuntimeError("Protocol 7 was not confirmed. No vehicle queries sent.")
                if any(x in raw.upper() for x in ("CAN ERROR", "BUS ERROR", "BUS INIT", "STOPPED", "UNABLE TO CONNECT")):
                    raise RuntimeError("Adapter reported a communication error; stopping")
                time.sleep(0.2)
        events.put({"status": "done"})
    except Exception as exc:
        events.put({"status": "error", "message": str(exc)})


def self_test():
    rows = frames("18DAF14506410080000001\r18 DA F1 11 06 41 00 BE 5F A8 13\r>")
    assert [r["module"] for r in rows] == ["GWM-Gateway", "ECM-EngineControl"]
    assert rows[1]["data_hex"] == "064100BE5FA813"
    assert frames("NO DATA\r>") == []
    assert not ({"04", "ATZ", "ATSH", "ATSP7", "ATWR"} & ALLOWED)
    print("Offline checks passed. No port opened and no commands sent.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--vehicle", action="store_true", help="Add finite read-only vehicle queries")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not re.fullmatch(r"COM[1-9][0-9]*", args.port.upper()):
        parser.error("Use a Windows port name such as COM3")
    if args.vehicle:
        answer = input("Truck parked, parking brake applied, vehicle powered; if idling, outdoors. OBDwiz/phone apps closed? Type READY: ")
        if answer.strip().upper() != "READY":
            print("Cancelled. No connection attempted.")
            return 1
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / ("colorado_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
    record = {"started": datetime.now().astimezone().isoformat(), "port": args.port,
              "vehicle_queries": args.vehicle, "verified_map": ECUS, "events": []}
    events = mp.Queue()
    process = mp.Process(target=worker, args=(args.port.upper(), args.vehicle, events))
    success = False
    try:
        process.start()
        while True:
            try:
                event = events.get(timeout=15)
            except queue.Empty:
                event = {"status": "error", "message": "No progress for 15 seconds. Connection worker stopped; no automatic retry."}
            record["events"].append(event)
            status = event["status"]
            if status == "reply":
                display = event["raw"].replace("\r\n", "\n").replace("\r", "\n")
                print(event["command"] + ":\n" + display.strip(), flush=True)
                for frame in event["frames"]:
                    print("  " + frame["header"] + " -> " + frame["module"], flush=True)
                    data = bytes.fromhex(frame["data_hex"])
                    if len(data) >= 4 and data[0] == 3 and data[1] == 0x7F:
                        print(f"  Request rejected: service {data[2]:02X}, response code {data[3]:02X}. No valid PID value.", flush=True)
            else:
                print(status + (": " + event["message"] if "message" in event else ""), flush=True)
            if status in ("done", "error"):
                success = status == "done"
                break
    except KeyboardInterrupt:
        record["events"].append({"status": "cancelled"})
        print("Cancelled.")
    finally:
        if process.pid is not None:
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
        log_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print("Log saved: " + str(log_path))
    return 0 if success else 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
