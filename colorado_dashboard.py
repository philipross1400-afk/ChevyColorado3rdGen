"""Colorado desktop dashboard: read-only ECM sensors and timestamped logs."""
import argparse
import csv
from datetime import datetime
import json
import math
import multiprocessing as mp
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox
import colorado_readonly as obd
from app_paths import logs_directory, app_directory
from colorado_log_viewer import LogReview
from colorado_features import (PRESETS, UNVERIFIED, gauge_catalog, normalize_layout,
                               read_json, write_json, append_marker, usable)

# PID: label, units, byte count, conversion, polling interval seconds
SENSORS = {
    0x0C: ("Engine speed", "RPM", 2, lambda b: int.from_bytes(b, "big") / 4, .5),
    0x0D: ("Vehicle speed", "mph", 1, lambda b: b[0] / 1.609344, .5),
    0x05: ("Coolant", "°F", 1, lambda b: (b[0] - 40) * 1.8 + 32, 2),
    0x42: ("ECM voltage", "V", 2, lambda b: int.from_bytes(b, "big") / 1000, 2),
    0x0B: ("Manifold absolute pressure", "kPa", 1, lambda b: b[0], 1),
    0x33: ("Barometric pressure", "kPa", 1, lambda b: b[0], 5),
    0x11: ("Absolute throttle", "%", 1, lambda b: b[0] * 100 / 255, 1),
    0x04: ("Calculated load", "%", 1, lambda b: b[0] * 100 / 255, 1),
    0x0F: ("Intake air temperature", "°F", 1, lambda b: (b[0] - 40) * 1.8 + 32, 2),
    0x10: ("Mass airflow", "g/s", 2, lambda b: int.from_bytes(b, "big") / 100, 2),
    0x0E: ("Ignition advance", "deg", 1, lambda b: b[0] / 2 - 64, 2),
    0x06: ("Short fuel trim B1", "%", 1, lambda b: (b[0] - 128) * 100 / 128, 2),
    0x07: ("Long fuel trim B1", "%", 1, lambda b: (b[0] - 128) * 100 / 128, 5),
    0x2F: ("Fuel level", "%", 1, lambda b: b[0] * 100 / 255, 5),
}
SETUP = ("ATH1", "ATS1", "ATCAF1", "ATCFC1", "ATSH18DA11F1", "ATCRA18DAF111")
TCM_ADDRESS = ("ATSH18DA18F1", "ATCRA18DAF118")
EXTENDED = {0x67: (3, (0x6701, 0x6702)), 0x68: (7, tuple(0x6800 + n for n in range(1, 7)))}
EXTENDED[0x87] = (5, (0x8701, 0x8702))
SENSORS[0x8701] = ("Extended manifold pressure A", "kPa", 0, None, .5)
SENSORS[0x8702] = ("Extended manifold pressure B", "kPa", 0, None, .5)
for number in range(1, 3):
    SENSORS[0x6700 + number] = (f"Extended coolant sensor {number}", "°F", 0, None, 3)
for number in range(1, 7):
    SENSORS[0x6800 + number] = (f"Intake temperature B{1 + (number-1)//3} S{1 + (number-1)%3}", "°F", 0, None, 3)
DISCOVERY = tuple(f"01{x:02X}" for x in range(0, 0xE1, 0x20))
obd.ALLOWED = frozenset(obd.ADAPTER + SETUP + TCM_ADDRESS + DISCOVERY + ("090A", "0167", "0168", "0187") + tuple(f"01{x:02X}" for x in SENSORS if x < 256))


def tcm_discovery(ask):
    """Finite identity/capability check, always restoring ECM addressing.

    Standard OBD support is not GM enhanced transmission coverage. No guessed
    enhanced IDs, security access, resets, code clearing or control requests.
    """
    result = {"type": "tcm_inventory", "header": "18DAF118", "supported": [],
              "name": None, "status": "Not completed", "queries": []}
    def setup(commands):
        for command in commands:
            if "OK" not in ask(command).replace(">", "").split():
                raise RuntimeError("Address setup not acknowledged: " + command)
    try:
        setup(TCM_ADDRESS)
        p = payload(ask("090A"), header="18DAF118")
        result["queries"].append("090A")
        if p[:3] != b"\x49\x0a\x01":
            raise ValueError("TCM name request rejected or unexpected")
        name = p[3:].replace(b"\0", b"").decode("ascii", errors="replace")
        if "TCM" not in name.upper():
            raise ValueError("Expected TCM name was not confirmed")
        result["name"] = name
        supported = set()
        for base in range(0, 0xE1, 0x20):
            command = f"01{base:02X}"
            result["queries"].append(command)
            bits = supported_bits(base, payload(ask(command), header="18DAF118"))
            supported.update(bits)
            if base + 32 not in bits:
                break
        result["supported"] = sorted(supported)
        result["status"] = "Identity and standard support read; enhanced transmission definitions still required"
    except ValueError as exc:
        result["status"] = str(exc)
    finally:
        setup(SETUP[-2:])
    return result

# Read-only profile: focus bandwidth on performance channels. Other sensors
# remain visible with an explicit not-polled status; nothing is fabricated.
PERFORMANCE = {0x0C: .2, 0x0D: .2, 0x0E: .2, 0x87: .2, 0x0B: .2,
               0x11: .5, 0x04: .5, 0x0F: 2, 0x05: 5, 0x42: 5, 0x33: 5}


def polling_intervals(support, profile):
    available = {p for p in SENSORS if p < 256} | set(EXTENDED)
    active = sorted(available & set(support))
    if profile == "Performance":
        return {p: PERFORMANCE[p] for p in active if p in PERFORMANCE}
    return {p: (.5 if p == 0x87 else 3 if p in EXTENDED else SENSORS[p][4]) for p in active}


def boost_value(values, now):
    b = values.get(0x33)
    if not b or b[0] is None or not 20 <= b[0] <= 120 or now - b[1] >= 12:
        return None
    for pid in (0x8701, 0x0B):
        a = values.get(pid)
        if (a and a[0] is not None and now - a[1] < 4 and a[0] > 0
                and not a[2].startswith("Suspect") and (pid != 0x0B or a[0] < 255)):
            return (a[0] - b[0]) / 6.894757, pid
    return None


def decode_extended(pid, raw):
    p = payload(raw)
    size, channels = EXTENDED[pid]
    if p[:2] != bytes((0x41, pid)) or len(p) != size + 2:
        raise ValueError("Unexpected extended PID or length")
    data = p[2:]
    result = []
    for bit, channel in enumerate(channels):
        if not data[0] & (1 << bit):
            result.append((channel, None, "Sensor not supported by response flags"))
        else:
            if pid == 0x87:
                start = 1 + bit * 2
                word = int.from_bytes(data[start:start + 2], "big")
                value = word / 32
                quality = "Suspect endpoint / verify" if word in (0, 65535) else "Received / unvalidated"
                result.append((channel, value, quality))
                continue
            byte = data[1 + bit]
            quality = "Suspect endpoint / verify" if byte == 0 else "Received / unvalidated"
            result.append((channel, (byte - 40) * 1.8 + 32, quality))
    return result


def payload(raw, header="18DAF111"):
    """Reassemble one explicitly selected ECU; never mix module frames."""
    buffer = bytearray()
    length = None
    sequence = 1
    for frame in obd.frames(raw):
        if frame["header"] != header:
            continue
        data = bytes.fromhex(frame["data_hex"])
        kind = data[0] >> 4
        if kind == 0:
            size = data[0] & 15
            if size and len(data) >= size + 1:
                return data[1:1 + size]
        elif kind == 1 and len(data) >= 2:
            length = ((data[0] & 15) << 8) | data[1]
            buffer = bytearray(data[2:])
            sequence = 1
        elif kind == 2 and length is not None:
            if data[0] & 15 != sequence:
                raise ValueError("Out-of-order ECM reply")
            buffer.extend(data[1:])
            sequence = (sequence + 1) % 16
            if len(buffer) >= length:
                return bytes(buffer[:length])
    raise ValueError("No complete ECM reply")


def decode(pid, raw):
    p = payload(raw)
    if p[:1] == b"\x7f":
        raise ValueError("ECU rejected request: " + p.hex(" ").upper())
    count = SENSORS[pid][2]
    if p[:2] != bytes((0x41, pid)) or len(p) != 2 + count:
        raise ValueError("Unexpected PID or length")
    b = p[2:]
    value = SENSORS[pid][3](b)
    # Flag suspect endpoint values instead of presenting them as validated data.
    quality = "Received / unvalidated"
    if (pid in (0x0F, 0x05) and b[0] == 0) or (pid == 0x33 and not 20 <= value <= 120) or (pid == 0x06 and b[0] == 0):
        quality = "Suspect endpoint / verify"
    return value, quality


def supported_bits(base, p):
    if len(p) != 6 or p[:2] != bytes((0x41, base)):
        raise ValueError("Invalid supported-PID response")
    bitmap = int.from_bytes(p[2:], "big")
    return {base + n for n in range(1, 33) if bitmap & (1 << (32 - n))}


def acquisition(port_name, events, stop, profile="Performance", check_tcm=True):
    try:
        import serial
        events.put({"type": "status", "text": "Opening " + port_name})
        with serial.Serial(port_name, 115200, timeout=.25, write_timeout=3) as port:
            def ask(command):
                if stop.is_set():
                    raise InterruptedError()
                raw = obd.exchange(port, command)
                events.put({"type": "raw", "time": datetime.now().astimezone().isoformat(), "command": command, "raw": raw})
                if any(t in raw.upper() for t in ("CAN ERROR", "BUS ERROR", "STOPPED", "UNABLE TO CONNECT")):
                    raise RuntimeError("Adapter reported a bus/connection error")
                return raw
            for command in obd.ADAPTER:
                raw = ask(command)
                if command == "ATDPN" and not ({"7", "A7"} & set(raw.replace(">", "").split())):
                    raise RuntimeError("Protocol 7 not confirmed; vehicle polling cancelled")
            for command in SETUP:
                if "OK" not in ask(command).replace(">", "").split():
                    raise RuntimeError("Adapter did not acknowledge " + command)
            if check_tcm:
                events.put({"type": "status", "text": "Checking TCM identity and standard parameter support"})
                events.put(tcm_discovery(ask))
            name = payload(ask("090A"))
            if name[:3] != b"\x49\x0a\x01":
                raise RuntimeError("Unexpected ECU name response")
            name = name[3:].replace(b"\0", b"").decode("ascii", errors="replace")
            if "ECM" not in name.upper():
                raise RuntimeError("ECM identity not confirmed")
            support = set()
            for base in range(0, 0xE1, 0x20):
                bits = supported_bits(base, payload(ask(f"01{base:02X}")))
                support.update(bits)
                if base + 32 not in bits:
                    break
            intervals = polling_intervals(support, profile)
            active = list(intervals)
            channels = [pid for pid in active if pid not in EXTENDED]
            for pid in active:
                if pid in EXTENDED:
                    channels.extend(EXTENDED[pid][1])
            events.put({"type": "inventory", "name": name, "supported": sorted(support), "active": channels, "profile": profile, "target_intervals": intervals})
            if not active:
                raise RuntimeError("No implemented sensors advertised by ECM")
            due = {pid: 0 for pid in active}
            events.put({"type": "status", "text": "Connected • recording read-only ECM data"})
            while not stop.is_set():
                pid = min(active, key=lambda p: due[p])
                if stop.wait(max(0, due[pid] - time.monotonic())):
                    break
                request_start = time.monotonic()
                raw = ask(f"01{pid:02X}")
                stamp = datetime.now().astimezone().isoformat()
                try:
                    samples = decode_extended(pid, raw) if pid in EXTENDED else [(pid, *decode(pid, raw))]
                    for channel, value, quality in samples:
                        events.put({"type": "sample", "time": stamp, "pid": channel, "value": value, "quality": quality})
                except ValueError as exc:
                    for channel in EXTENDED[pid][1] if pid in EXTENDED else (pid,):
                        events.put({"type": "sample", "time": stamp, "pid": channel, "value": None, "quality": str(exc)})
                due[pid] = time.monotonic() + intervals[pid]
                # No bursts, parallel requests or automatic retries. Cap at
                # ten request starts/second, and allow ECU reply time to dominate.
                stop.wait(max(.03, .1 - (time.monotonic() - request_start)))
    except InterruptedError:
        pass
    except Exception as exc:
        events.put({"type": "error", "text": str(exc)})
    finally:
        events.put({"type": "done"})


BG = "#101720"
CARD = "#182331"
TEXT = "#e8eff6"
MUTED = "#9aabbc"
ACCENT = "#53d8d1"


class Dial(tk.Canvas):
    """Display only: no simulated values, interpolation or vehicle queries."""
    def __init__(self, parent, title, unit, low, high, color=ACCENT):
        super().__init__(parent, bg=CARD, highlightthickness=0, width=220, height=190)
        self.title, self.unit, self.low, self.high, self.color = title, unit, low, high, color
        self.value = None
        self.detail = "Waiting for data"
        self.bind("<Configure>", lambda e: self.draw())

    def update_value(self, value, detail):
        state = (None if value is None else round(value, 1), detail)
        if state != (self.value, self.detail):
            self.value, self.detail = state
            self.draw()

    def draw(self):
        self.delete("all")
        width, height = max(180, self.winfo_width()), max(170, self.winfo_height())
        cx, cy, radius = width/2, height*.58, min(width*.34, height*.32)
        box = (cx-radius, cy-radius, cx+radius, cy+radius)
        self.create_text(cx, 18, text=self.title, fill=MUTED, font=("Segoe UI", 10, "bold"))
        self.create_arc(*box, start=0, extent=180, style="arc", outline="#314154", width=9)
        for n in range(5):
            fraction = n/4
            angle = math.pi*(1-fraction)
            x, y = cx+radius*math.cos(angle), cy-radius*math.sin(angle)
            self.create_line(x, y, cx+(radius-10)*math.cos(angle), cy-(radius-10)*math.sin(angle), fill=MUTED, width=2)
            number = self.low+(self.high-self.low)*fraction
            label = f"{number/1000:g}k" if self.high >= 1000 else f"{number:g}"
            self.create_text(cx+(radius+15)*math.cos(angle), cy-(radius+15)*math.sin(angle), text=label, fill=MUTED, font=("Segoe UI", 8))
        if self.value is not None:
            fraction = max(0, min(1, (self.value-self.low)/(self.high-self.low)))
            angle = math.pi*(1-fraction)
            self.create_line(cx, cy, cx+(radius-15)*math.cos(angle), cy-(radius-15)*math.sin(angle), fill=self.color, width=3, tags="needle")
            self.create_oval(cx-4, cy-4, cx+4, cy+4, fill=self.color, outline="")
            reading = f"{self.value:,.0f}" if self.unit in ("RPM", "mph") else f"{self.value:.1f}"
        else:
            reading = "—"
        self.create_text(cx, height*.70, text=reading, fill=TEXT if self.value is not None else MUTED, font=("Segoe UI", 25, "bold"))
        self.create_text(cx, height*.84, text=self.unit, fill=MUTED, font=("Segoe UI", 9))
        detail = self.detail
        if self.value is not None and not self.low <= self.value <= self.high:
            detail = "Beyond dial range • " + detail
        self.create_text(cx, height*.94, text=detail, fill=self.color if self.value is not None else MUTED, font=("Segoe UI", 8), width=width-12)


class Dashboard:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.logs = []
        self.values = {}
        self.session_folder = None
        self.catalog = gauge_catalog(SENSORS)
        self.layout_path = app_directory() / "dashboard_settings.json"
        self.layout = normalize_layout(read_json(self.layout_path), self.catalog)
        root.title("Colorado • Dashboard & Recordings v0.7")
        root.geometry("1180x900")
        root.minsize(1000, 800)
        root.configure(bg=BG)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=MUTED)
        style.configure("TButton", background="#26394b", foreground=TEXT, padding=(12, 7))
        style.map("TButton", background=[("active", "#355268"), ("disabled", "#1a2531")], foreground=[("disabled", "#667787")])
        style.configure("TCheckbutton", background=BG, foreground=MUTED)
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", "#667787")])
        style.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT)
        style.configure("TCombobox", fieldbackground=CARD, foreground=TEXT, arrowcolor=ACCENT)
        style.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXT)])
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10), background=CARD, fieldbackground=CARD, foreground=TEXT, borderwidth=0)
        style.map("Treeview", background=[("selected", "#28485b")], foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background="#223344", foreground=MUTED, font=("Segoe UI", 9, "bold"), padding=7)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=CARD, foreground=MUTED, padding=(20,10))
        style.map("TNotebook.Tab", background=[("selected", "#294455")], foreground=[("selected", TEXT)])
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True)
        outer = ttk.Frame(tabs, padding=18)
        tabs.add(outer, text="Live Dashboard")
        self.log_review = LogReview(tabs)
        tabs.add(self.log_review, text="Log Review")
        capability = ttk.Frame(tabs, padding=18)
        tabs.add(capability, text="Vehicle Capabilities")
        ttk.Label(capability, text="VEHICLE / CAPABILITIES", font=("Segoe UI", 23, "bold"), foreground=TEXT).pack(anchor="w")
        self.capability_info = tk.StringVar(value="No discovery this session. Connect to read ECM identity and advertised PID support.")
        ttk.Label(capability, textvariable=self.capability_info, wraplength=1000).pack(anchor="w", pady=12)
        ttk.Label(capability, text="Parent PID support does not guarantee an extended sensor channel is present. Channel flags and sample quality appear after polling.", wraplength=1000).pack(anchor="w")
        self.capabilities = ttk.Treeview(capability, columns=("pid", "support", "quality"), height=24)
        self.capabilities.heading("#0", text="Signal")
        for key, label in (("pid", "Source PID"), ("support", "Discovery / polling"), ("quality", "Latest sample quality")):
            self.capabilities.heading(key, text=label)
        self.capabilities.column("#0", width=260)
        self.capabilities.column("pid", width=110)
        self.capabilities.pack(fill="both", expand=True, pady=12)
        for pid, spec in SENSORS.items():
            self.capabilities.insert("", "end", iid=str(pid), text=spec[0], values=(f"01{pid if pid < 256 else pid >> 8:02X}", "Not checked", "—"))
        for index, label in enumerate(UNVERIFIED):
            self.capabilities.insert("", "end", iid=f"unverified{index}", text=label, values=("—", "Definition unverified", "Unavailable"))
        ttk.Label(outer, text="COLORADO  /  LIVE", foreground=TEXT, font=("Segoe UI", 23, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Read-only monitoring  •  Recorded values remain unvalidated  •  v0.7").pack(anchor="w", pady=(0, 6))
        ttk.Button(outer, text="Customize dashboard…", command=self.customize).pack(anchor="e")
        self.gauge_row = ttk.Frame(outer)
        self.gauge_row.pack(fill="x", pady=(0, 14))
        self.gauges = {}
        self.build_gauges()
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        ttk.Label(bar, text="Outgoing port").pack(side="left")
        self.port = tk.StringVar(value="COM3")
        self.port_entry = ttk.Entry(bar, textvariable=self.port, width=10)
        self.port_entry.pack(side="left", padx=8)
        self.start_button = ttk.Button(bar, text="Connect & record", command=self.start)
        self.start_button.pack(side="left", padx=5)
        self.stop_button = ttk.Button(bar, text="Stop & save", command=self.finish, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        settings = ttk.Frame(outer)
        settings.pack(fill="x", pady=8)
        ttk.Label(settings, text="Logging profile").pack(side="left")
        self.profile = tk.StringVar(value="Performance")
        self.profile_box = ttk.Combobox(settings, textvariable=self.profile, values=("Performance", "Overview"), state="readonly", width=14)
        self.profile_box.pack(side="left", padx=8)
        ttk.Label(settings, text="Fuel in tank").pack(side="left")
        self.fuel = tk.StringVar(value="Unknown")
        self.fuel_box = ttk.Combobox(settings, textvariable=self.fuel, values=("Unknown", "87 AKI", "89 AKI", "91 AKI", "93 AKI", "Mixed / see notes"), state="readonly", width=18)
        self.fuel_box.pack(side="left", padx=8)
        self.notes = tk.StringVar()
        ttk.Label(settings, text="Notes").pack(side="left")
        self.notes_box = ttk.Entry(settings, textvariable=self.notes, width=28)
        self.notes_box.pack(side="left", padx=8)
        annotations = ttk.Frame(outer)
        annotations.pack(fill="x", pady=6)
        self.recording_name = tk.StringVar()
        ttk.Label(annotations, text="Recording name").pack(side="left")
        self.name_box = ttk.Entry(annotations, textvariable=self.recording_name, width=28)
        self.name_box.pack(side="left", padx=8)
        self.marker_label = tk.StringVar(value="Observation")
        marker = ttk.Combobox(annotations, textvariable=self.marker_label, values=("Observation", "Hesitation", "Shift", "Temperature rise"), width=20)
        marker.pack(side="left", padx=8)
        self.marker_note = tk.StringVar()
        ttk.Entry(annotations, textvariable=self.marker_note, width=26).pack(side="left", padx=4)
        self.marker_button = ttk.Button(annotations, text="Mark event", command=self.mark_event, state="disabled")
        self.marker_button.pack(side="left", padx=4)
        self.check_tcm = tk.BooleanVar(value=True)
        self.tcm_box = ttk.Checkbutton(outer, variable=self.check_tcm, text="Check TCM identity at connect (discovery only; no live slip readings)")
        self.tcm_box.pack(anchor="w")
        self.tcm_status = tk.StringVar(value="TCM: not checked this session • temperature/gear/slip definitions unverified")
        ttk.Label(outer, textvariable=self.tcm_status, wraplength=1000).pack(anchor="w", pady=4)
        self.ready = tk.BooleanVar()
        self.ready_box = ttk.Checkbutton(outer, variable=self.ready, text="Parked setup confirmed • parking brake applied • other OBD apps closed • outdoors if idling")
        self.ready_box.pack(anchor="w", pady=4)
        ttk.Label(outer, text="Set up before moving. Passenger operation or unattended recording only; review after parking.").pack(anchor="w")
        self.status = tk.StringVar(value="Disconnected • no queries sent")
        ttk.Label(outer, textvariable=self.status, foreground=ACCENT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=6)
        self.sampling = tk.StringVar(value="Measured intervals appear after samples arrive; target intervals are not guaranteed.")
        ttk.Label(outer, textvariable=self.sampling, wraplength=1100).pack(anchor="w", pady=(0,6))
        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=("value", "unit", "quality", "age"), height=6)
        self.table.heading("#0", text="Sensor")
        self.table.column("#0", width=270)
        for key, label, width in (("value", "Value", 100), ("unit", "Units", 70), ("quality", "Status", 330), ("age", "Age", 65)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        for pid, spec in SENSORS.items():
            self.table.insert("", "end", iid=str(pid), text=spec[0], values=("—", spec[1], "Not checked", "—"))
        self.table.insert("", "end", iid="boost", text="Calculated boost / vacuum", values=("—", "psi", "Needs valid MAP and barometric pressure", "—"))
        for iid, label in (("knock", "Knock retard"), ("trans", "Transmission temperature / gear"), ("slip", "Converter / clutch slip")):
            self.table.insert("", "end", iid=iid, text=label, values=("—", "—", "GM definitions not verified", "—"))
        self.inventory = tk.StringVar(value="ECM 11 • TCM 18 • BSCM 28 • BCM 40 • GWM 45 (previously verified)")
        ttk.Label(outer, textvariable=self.inventory, wraplength=1000).pack(anchor="w", pady=8)
        self.location = tk.StringVar(value="Logs will be saved beside this app in dashboard_logs.")
        ttk.Label(outer, textvariable=self.location, wraplength=1000).pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.tick)

    def build_gauges(self):
        for child in self.gauge_row.winfo_children():
            child.destroy()
        self.gauges = {}
        for column in range(4):
            self.gauge_row.columnconfigure(column, weight=0)
        for column, key in enumerate(self.layout["signals"]):
            title, unit, low, high, _ = self.catalog[key]
            self.gauge_row.columnconfigure(column, weight=1, uniform="dial")
            dial = Dial(self.gauge_row, title.upper(), unit, low, high, "#ffbe68" if key == "boost" else ACCENT)
            dial.config(height=240 if self.layout["large_cards"] else 190)
            dial.grid(row=0, column=column, sticky="ew", padx=4)
            self.gauges[key] = dial

    def customize(self):
        window = tk.Toplevel(self.root)
        window.title("Dashboard layout")
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)
        names = {key: spec[0] for key, spec in self.catalog.items()}
        reverse = {value: key for key, value in names.items()}
        choices = [tk.StringVar(value=names.get(key, "Hidden")) for key in self.layout["signals"]]
        choices += [tk.StringVar(value="Hidden") for _ in range(4-len(choices))]
        ttk.Label(frame, text="Choose left-to-right order. Hide slots you do not need.").pack(anchor="w")
        preset = tk.StringVar(value="Everyday")
        box = ttk.Combobox(frame, textvariable=preset, values=tuple(PRESETS), state="readonly")
        box.pack(fill="x", pady=8)
        def use_preset(_):
            for variable, key in zip(choices, PRESETS[preset.get()]):
                variable.set(names[key])
        box.bind("<<ComboboxSelected>>", use_preset)
        for variable in choices:
            ttk.Combobox(frame, textvariable=variable, values=("Hidden", *names.values()), state="readonly", width=36).pack(fill="x", pady=4)
        large = tk.BooleanVar(value=self.layout["large_cards"])
        ttk.Checkbutton(frame, text="Larger gauge cards", variable=large).pack(anchor="w", pady=8)
        def save():
            layout = normalize_layout({"signals": [reverse[v.get()] for v in choices if v.get() in reverse], "large_cards": large.get()}, self.catalog)
            try:
                write_json(self.layout_path, layout)
                self.layout = layout
                self.build_gauges()
                window.destroy()
            except OSError as exc:
                messagebox.showerror("Could not save layout", str(exc), parent=window)
        ttk.Button(frame, text="Save layout", command=save).pack(anchor="e", pady=8)

    def mark_event(self):
        if self.process is None or self.session_folder is None:
            return
        try:
            event = append_marker(self.session_folder, self.marker_label.get(), self.marker_note.get())
            self.marker_note.set("")
            self.location.set(f"Event saved: {event['label']} • {self.session_folder}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not mark event", str(exc))

    def start(self):
        import re
        if not self.ready.get():
            messagebox.showinfo("Parked setup", "Confirm the parked setup checkbox first.")
            return
        port = self.port.get().strip().upper()
        if not re.fullmatch(r"COM[1-9][0-9]*", port):
            messagebox.showerror("Port", "Enter an outgoing port such as COM3.")
            return
        folder = logs_directory() / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            folder.mkdir(parents=True)
            metadata = {"schema_version": 1, "app_version": "0.7", "name": self.recording_name.get().strip(), "started": datetime.now().astimezone().isoformat(), "port": port,
                        "profile": self.profile.get(), "fuel_in_tank": self.fuel.get(), "notes": self.notes.get(),
                        "knock_and_transmission": "Not verified; no enhanced requests sent"}
            metadata["tcm_discovery_enabled"] = self.check_tcm.get()
            (folder / "session.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            self.session_folder = folder
            self.raw_log = (folder / "raw.jsonl").open("w", encoding="utf-8")
            self.csv_log = (folder / "sensors.csv").open("w", newline="", encoding="utf-8-sig")
            self.logs = [self.raw_log, self.csv_log]
            self.writer = csv.writer(self.csv_log)
            self.writer.writerow(("timestamp", "ecu", "pid", "sensor", "value", "units", "quality"))
            self.csv_log.flush()
            self.events, self.stop = mp.Queue(), mp.Event()
            self.failure = None
            self.values.clear()
            self.capability_info.set("Discovering this session's ECM identity and PID support…")
            for pid in SENSORS:
                self.capabilities.set(str(pid), "support", "Discovering")
                self.capabilities.set(str(pid), "quality", "—")
            self.tcm_status.set("TCM: checking at connection" if self.check_tcm.get() else "TCM: check disabled; ECM-only session")
            self.sample_gaps = {}
            self.sample_times = {}
            for pid, spec in SENSORS.items():
                self.table.item(str(pid), values=("—", spec[1], "Discovering", "—"))
            self.table.item("boost", values=("—", "psi", "Waiting for valid pressure readings", "—"))
            self.process = mp.Process(target=acquisition, args=(port, self.events, self.stop, self.profile.get(), self.check_tcm.get()))
            self.last_event = time.monotonic()
            self.process.start()
            self.location.set("Recording to " + str(folder))
            self.start_button.config(state="disabled")
            self.port_entry.config(state="disabled")
            self.ready_box.config(state="disabled")
            self.tcm_box.config(state="disabled")
            for control in (self.profile_box, self.fuel_box, self.notes_box):
                control.config(state="disabled")
            self.stop_button.config(state="normal")
            self.marker_button.config(state="normal")
            self.name_box.config(state="disabled")
        except Exception as exc:
            self.finish()
            messagebox.showerror("Could not start", str(exc))

    def finish(self, text="Stopped • logs saved; displayed values are historical"):
        if self.process is not None:
            self.stop.set()
            self.process.join(timeout=.3)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=1)
            self.process = None
        for handle in self.logs:
            handle.close()
        self.logs = []
        self.start_button.config(state="normal")
        self.port_entry.config(state="normal")
        self.ready_box.config(state="normal")
        self.tcm_box.config(state="normal")
        self.profile_box.config(state="readonly")
        self.fuel_box.config(state="readonly")
        self.notes_box.config(state="normal")
        self.stop_button.config(state="disabled")
        self.marker_button.config(state="disabled")
        self.name_box.config(state="normal")
        self.status.set(text)
        self.log_review.refresh()

    def handle(self, event):
        self.raw_log.write(json.dumps(event) + "\n")
        self.raw_log.flush()
        kind = event["type"]
        if kind == "status":
            self.status.set(event["text"])
        elif kind == "tcm_inventory":
            self.tcm_status.set("TCM: " + (event["name"] or "identity unconfirmed") + " • " + event["status"])
            report_path = Path(self.raw_log.name).parent / "tcm_inventory.json"
            report_path.write_text(json.dumps(event, indent=2), encoding="utf-8")
        elif kind == "error":
            self.status.set("Error • " + event["text"])
            self.failure = event["text"]
        elif kind == "pressure_capture":
            self.table.item("boost", values=("—", "psi", "Extended MAP logged; pressure decoding pending", "—"))
        elif kind == "inventory":
            metadata = read_json(self.session_folder / "session.json")
            metadata["capabilities"] = event
            write_json(self.session_folder / "session.json", metadata)
            self.capability_info.set(f"{event['name']} • {event['profile']} • ECM 18DAF111 • saved with this recording")
            active = set(event["active"])
            for pid, spec in SENSORS.items():
                source_pid = pid if pid < 256 else pid >> 8
                quality = "Waiting" if pid in active else "Not polled in this profile" if source_pid in event["supported"] else "Not advertised by ECM"
                self.table.item(str(pid), values=("—", spec[1], quality, "—"))
                self.capabilities.set(str(pid), "support", quality)
            self.inventory.set(event["name"] + " • advertised PIDs: " + " ".join(f"{p:02X}" for p in event["supported"]))
        elif kind == "sample":
            pid, value = event["pid"], event["value"]
            spec = SENSORS[pid]
            sample_time = datetime.fromisoformat(event["time"]).timestamp()
            if pid in self.sample_times:
                gaps = self.sample_gaps.setdefault(pid, [])
                gaps.append(sample_time - self.sample_times[pid])
                del gaps[:-20]
            self.sample_times[pid] = sample_time
            import statistics
            rates = [f"{SENSORS[p][0]}: {statistics.median(self.sample_gaps[p]):.2f} s" for p in (0x0C, 0x0D, 0x0E, 0x8701) if self.sample_gaps.get(p)]
            if rates:
                self.sampling.set("Measured median interval • " + " | ".join(rates))
            self.values[pid] = (value, time.monotonic(), event["quality"])
            self.capabilities.set(str(pid), "quality", event["quality"])
            pid_label = f"01{pid:02X}" if pid < 256 else f"01{pid >> 8:02X}/sensor{pid & 255}"
            self.writer.writerow((event["time"], "18DAF111", pid_label, spec[0], "" if value is None else value, spec[1], event["quality"]))
            self.csv_log.flush()
            self.table.item(str(pid), values=("—" if value is None else f"{value:.2f}", spec[1], event["quality"], "0 s"))
            now = time.monotonic()
            boost = boost_value(self.values, now)
            if pid in (0x8701, 0x0B, 0x33) and boost is not None:
                self.writer.writerow((event["time"], "18DAF111", "derived", "Calculated boost / vacuum", boost[0], "psi", f"Derived from asynchronous MAP {boost[1]:04X} and barometric pressure; unvalidated"))
                self.csv_log.flush()
        elif kind == "done":
            self.finish("Stopped • " + self.failure if getattr(self, "failure", None) else "Stopped • logs saved")

    def tick(self):
        if self.process is not None:
            try:
                for _ in range(100):
                    event = self.events.get_nowait()
                    self.last_event = time.monotonic()
                    self.handle(event)
                    if self.process is None:
                        break
            except queue.Empty:
                pass
            except Exception as exc:
                self.finish("Stopped • log or display error: " + str(exc))
            if self.process is not None and time.monotonic() - self.last_event > 15:
                self.finish("Stopped • no progress for 15 seconds. Toggle laptop Bluetooth before retrying.")
        now = time.monotonic()
        for key, dial in self.gauges.items():
            if key == "boost":
                derived = boost_value(self.values, now) if self.process is not None else None
                dial.update_value(derived[0] if derived else None, "Calculated • asynchronous pressure" if derived else "Waiting / unavailable")
                continue
            pid = self.catalog[key][4]
            sample = self.values.get(pid)
            if self.process is None:
                dial.update_value(None, "Disconnected")
            elif sample is None:
                dial.update_value(None, self.table.set(str(pid), "quality"))
            elif now-sample[1] > max(3, SENSORS[pid][4]*3) or not usable(sample[0], sample[2]):
                dial.update_value(None, "Stale / " + sample[2])
            else:
                dial.update_value(sample[0], f"Unvalidated • {now-sample[1]:.1f}s old")
        for pid, (value, stamp, quality) in self.values.items():
            age = now - stamp
            self.table.set(str(pid), "age", f"{age:.0f} s")
            if age > max(5, SENSORS[pid][4] * 3):
                self.table.set(str(pid), "quality", "Stale • " + quality)
        # Gauge pressure is meaningful only when both inputs are present,
        # recent, and not flagged. It is never substituted for a missing sensor.
        boost = boost_value(self.values, now) if self.process is not None else None
        if boost is not None:
            self.table.item("boost", values=(f"{boost[0]:.2f}", "psi", "Calculated MAP minus barometric pressure / unvalidated", "—"))
        else:
            self.table.item("boost", values=("—", "psi", "Unavailable • needs recent valid pressure inputs", "—"))
        self.root.after(100, self.tick)

    def close(self):
        self.finish()
        self.root.destroy()


def self_test():
    tcm_name = "18DAF1181017490A0154434D\r18DAF11821002D5472616E73\r18DAF118226D69734374726C\r18DAF1182300000000000000\r>"
    assert payload(tcm_name, "18DAF118")[3:].replace(b"\0", b"") == b"TCM-TransmisCtrl"
    requests = []
    def fake_ask(command):
        requests.append(command)
        return {"090A": tcm_name, "0100": "18DAF11806410080000001", "0120": "18DAF11806412000000000"}.get(command, "OK\r>")
    inventory = tcm_discovery(fake_ask)
    assert inventory["name"] == "TCM-TransmisCtrl" and inventory["supported"] == [1, 32]
    assert requests[-2:] == list(SETUP[-2:])
    assert all(c.startswith(("AT", "01", "09")) for c in requests)
    requests.clear()
    def rejected_ask(command):
        requests.append(command)
        return "18DAF118037F0922\r>" if command == "090A" else "OK\r>"
    assert tcm_discovery(rejected_ask)["name"] is None
    assert requests[-2:] == list(SETUP[-2:])
    requests.clear()
    def timed_out_ask(command):
        requests.append(command)
        if command == "090A":
            raise TimeoutError("simulated timeout")
        return "OK\r>"
    try:
        tcm_discovery(timed_out_ask)
    except TimeoutError:
        pass
    else:
        raise AssertionError("Communication timeout swallowed")
    assert requests[-2:] == list(SETUP[-2:])
    pressures = decode_extended(0x87, "18DAF111074187010C890000")
    assert pressures[0][1] == 100.28125 and pressures[1][1] is None
    assert decode_extended(0x87, "18DAF111074187030C800640")[1][1] == 50
    assert decode_extended(0x87, "18DAF11107418701FFFF0000")[0][2].startswith("Suspect")
    assert abs(boost_value({0x8701: (170, 10, "Received / unvalidated"), 0x33: (100, 10, "Received / unvalidated")}, 11)[0] - 10.152641) < .001
    assert boost_value({0x8701: (170, 1, "Received / unvalidated"), 0x33: (100, 10, "Received / unvalidated")}, 11) is None
    assert boost_value({0x8701: (170, 10, "Received / unvalidated"), 0x33: (255, 10, "Suspect")}, 11) is None
    assert boost_value({0x8701: (0, 10, "Suspect"), 0x33: (100, 10, "Received / unvalidated")}, 11) is None
    support = {0x0C, 0x0D, 0x0E, 0x05, 0x2F, 0x67, 0x87}
    assert 0x2F not in polling_intervals(support, "Performance")
    assert 0x2F in polling_intervals(support, "Overview")
    assert set(polling_intervals({0x0C}, "Performance")) == {0x0C}
    for bad_pressure in ("18DAF111064187010C8900", "18DAF111037F0122"):
        try:
            decode_extended(0x87, bad_pressure)
        except ValueError:
            pass
        else:
            raise AssertionError("Malformed pressure reply accepted")
    assert decode_extended(0x67, "18DAF111054167035064")[0][1] == 104
    assert decode_extended(0x67, "18DAF111054167015064")[1][1] is None
    temps = decode_extended(0x68, "18DAF111100941680700005A\r18DAF1112100000000000000")
    assert temps[0][2].startswith("Suspect") and temps[2][1] == 122
    assert temps[3][1] is None
    assert decode(0x0C, "18 DA F1 11 04 41 0C 1F 40\r>")[0] == 2000
    assert decode(0x05, "18DAF11103410550\r>")[0] == 104
    assert decode(0x33, "18DAF111034133FF\r>")[1].startswith("Suspect")
    assert supported_bits(0, bytes.fromhex("410080000001")) == {1, 32}
    capture = "18DAF1111017490A0145434D\r18DAF11121002D456E67696E\r18DAF1112265436F6E74726F\r18DAF111236C000000000000\r>"
    assert payload(capture)[3:].replace(b"\0", b"") == b"ECM-EngineControl"
    for bad in ("18DAF12804410C1F40", "18DAF111037F0122", "18DAF11103410C00"):
        try:
            decode(0x0C, bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid or rejected RPM reply was accepted")
    try:
        payload(capture.replace("112100", "112200"))
    except ValueError:
        pass
    else:
        raise AssertionError("Out-of-order frames accepted")
    assert all(c.startswith(("AT", "01", "09")) for c in obd.ALLOWED)
    print("Passed offline decoding, support discovery, ECU identity, rejection and frame-order checks. No hardware contacted.")


if __name__ == "__main__":
    mp.freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--test-report", help="Save offline check result as JSON, without opening the interface")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        from colorado_log_viewer import self_test as review_self_test
        review_self_test()
        if args.test_report:
            Path(args.test_report).write_text(json.dumps({"version":"0.6", "offline_checks":"passed", "hardware_contacted":False}), encoding="utf-8")
    else:
        Dashboard(tk.Tk()).root.mainloop()
