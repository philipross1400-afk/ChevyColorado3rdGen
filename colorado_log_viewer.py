"""Read-only review of Colorado CSV recordings; never opens a vehicle port."""
import csv
import bisect
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import statistics
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog
from app_paths import logs_directory
from colorado_features import load_markers, sample_at, read_json, write_json

BG, CARD, TEXT, MUTED = "#101720", "#182331", "#e8eff6", "#9aabbc"
COLORS = ("#53d8d1", "#ffbe68", "#aaabff")
WINDOWS = {"Entire recording": None, "30 seconds": 30, "1 minute": 60, "5 minutes": 300}
DETAIL = {"Original samples": 0, "30-second averages": 30, "1-minute averages": 60, "5-minute averages": 300}


def load_recording(path):
    path = Path(path)
    if path.is_dir():
        path = path / "sensors.csv"
    if path.stat().st_size > 64*1024*1024:
        raise ValueError("This recording exceeds the 64 MB import limit.")
    groups, units, bad, total = {}, {}, 0, 0
    first, last, started = math.inf, -math.inf, None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"timestamp", "sensor", "value", "units", "quality"}.issubset(reader.fieldnames or ()):
            raise ValueError("Choose a Colorado sensors.csv file. This file has a different column format.")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Duplicate CSV columns")
        for row in reader:
            total += 1
            if total > 500000:
                raise ValueError("This recording exceeds the 500,000-row import limit. Split it before importing.")
            try:
                stamp = datetime.fromisoformat(row["timestamp"])
                if stamp.tzinfo is None:
                    raise ValueError("Timestamp needs timezone")
                time = stamp.timestamp()
                name, unit = row["sensor"].strip(), row["units"].strip()
                if not name:
                    raise ValueError("Missing sensor")
                # Split a name when files contain different units or ECUs.
                key = (name, unit, row.get("ecu", ""), row.get("pid", ""))
                quality = row["quality"] or "Unknown quality"
                value = float(row["value"]) if row["value"] else None
                if value is not None and not math.isfinite(value):
                    raise ValueError("Non-finite reading")
                if started is None or time < first:
                    started = stamp
                first, last = min(first, time), max(last, time)
                good = quality == "Received / unvalidated" or quality.startswith("Derived from asynchronous")
                if not good:
                    value = None
                groups.setdefault(key, []).append((time, value, quality))
                units[key] = unit
            except (ValueError, TypeError, KeyError, AttributeError):
                bad += 1
    if first == math.inf or not groups:
        raise ValueError("No readable timestamped samples found.")
    for samples in groups.values():
        samples.sort(key=lambda p: p[0])
    metadata = {}
    metadata_path = path.parent / "session.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            metadata = {}
    except (OSError, ValueError):
        pass
    events, bad_events = load_markers(path.parent)
    return {"path": path, "series": groups, "units": units, "start": first, "end": last,
            "started": started, "metadata": metadata, "bad_rows": bad, "rows": total,
            "events": events, "bad_events": bad_events}


def window_bounds(start, end, width, page):
    if width is None:
        return start, end, 0, 1
    pages = max(1, math.ceil((end-start)/width))
    page = max(0, min(page, pages-1))
    return start+page*width, min(end, start+(page+1)*width), page, pages


def select_samples(samples, low, high, include_end=False):
    return [p for p in samples if low <= p[0] and (p[0] <= high if include_end else p[0] < high)]


def aggregate(samples, width, origin):
    """Mean + min/max/count in elapsed-time buckets; never invent empty buckets."""
    if not width:
        return [(t, v, v, v, 1) for t,v,_ in samples if v is not None]
    buckets = {}
    for t,v,_ in samples:
        if v is not None:
            buckets.setdefault(math.floor((t-origin)/width), []).append((t,v))
    return [(statistics.mean(t for t,v in bucket), statistics.mean(v for t,v in bucket),
             min(v for t,v in bucket), max(v for t,v in bucket), len(bucket))
            for _,bucket in sorted(buckets.items())]


def elapsed(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds//60:02d}:{seconds%60:02d}"


class LogReview(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=18)
        self.recording = None
        self.paths, self.names, self.keys = [], [], []
        self.page = 0
        self.loading = False
        self.results = queue.Queue()
        self.playing = False
        self.play_clock = time.monotonic()
        ttk.Label(self, text="RECORDINGS  /  REVIEW", foreground=TEXT, font=("Segoe UI", 23, "bold")).pack(anchor="w")
        ttk.Label(self, text="Review saved data without connecting to the truck. Sensor CSV files stay unchanged.").pack(anchor="w", pady=(0,12))
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Recording").pack(side="left")
        self.record_choice = ttk.Combobox(row, state="readonly", width=38)
        self.record_choice.pack(side="left", padx=8)
        ttk.Button(row, text="Load recording", command=self.open_selected).pack(side="left", padx=4)
        ttk.Button(row, text="Import CSV…", command=self.import_csv).pack(side="left", padx=4)
        ttk.Button(row, text="Refresh list", command=self.refresh).pack(side="left", padx=4)
        ttk.Button(row, text="Edit name / notes…", command=self.edit_notes).pack(side="left", padx=4)
        self.info = tk.StringVar(value="Choose an existing recording or import a Colorado CSV log.")
        ttk.Label(self, textvariable=self.info, wraplength=1050, foreground="#53d8d1").pack(anchor="w", pady=12)
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Browse").pack(side="left")
        self.window = tk.StringVar(value="Entire recording")
        box = ttk.Combobox(row, textvariable=self.window, values=tuple(WINDOWS), state="readonly", width=18)
        box.pack(side="left", padx=8)
        box.bind("<<ComboboxSelected>>", lambda e: self.reset_page())
        self.prev = ttk.Button(row, text="‹ Previous", command=lambda: self.move(-1))
        self.prev.pack(side="left", padx=3)
        self.next = ttk.Button(row, text="Next ›", command=lambda: self.move(1))
        self.next.pack(side="left", padx=3)
        ttk.Label(row, text="Graph detail").pack(side="left", padx=(14,0))
        self.detail = tk.StringVar(value="Original samples")
        box = ttk.Combobox(row, textvariable=self.detail, values=tuple(DETAIL), state="readonly", width=22)
        box.pack(side="left", padx=8)
        box.bind("<<ComboboxSelected>>", lambda e: self.redraw())
        self.range_label = tk.StringVar(value="No recording loaded")
        ttk.Label(self, textvariable=self.range_label).pack(anchor="w", pady=8)
        playback = ttk.Frame(self)
        playback.pack(fill="x", pady=6)
        self.play_button = ttk.Button(playback, text="Play", command=self.toggle_play)
        self.play_button.pack(side="left")
        self.play_speed = tk.StringVar(value="1×")
        ttk.Combobox(playback, textvariable=self.play_speed, values=("0.5×", "1×", "2×", "4×"), state="readonly", width=5).pack(side="left", padx=8)
        self.cursor = tk.DoubleVar(value=0)
        self.scrubber = ttk.Scale(playback, variable=self.cursor, from_=0, to=1, command=self.seek)
        self.scrubber.pack(side="left", fill="x", expand=True, padx=8)
        self.cursor_label = tk.StringVar(value="00:00")
        ttk.Label(playback, textvariable=self.cursor_label, width=10).pack(side="left")
        self.snapshot = tk.StringVar(value="Playback uses the last recorded sample at or before the cursor; no interpolation.")
        ttk.Label(self, textvariable=self.snapshot, foreground=TEXT, wraplength=1100).pack(anchor="w", pady=6)
        event_row = ttk.Frame(self)
        event_row.pack(fill="x", pady=4)
        ttk.Label(event_row, text="Event markers").pack(side="left")
        self.event_choice = ttk.Combobox(event_row, state="readonly", width=70)
        self.event_choice.pack(side="left", fill="x", expand=True, padx=8)
        self.event_choice.bind("<<ComboboxSelected>>", self.jump_event)
        self.event_note = tk.StringVar()
        ttk.Label(self, textvariable=self.event_note, wraplength=1100).pack(anchor="w")
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        side = ttk.Frame(body, width=260)
        side.pack(side="left", fill="y", padx=(0,12))
        ttk.Label(side, text="SIGNALS", foreground=TEXT, font=("Segoe UI",10,"bold")).pack(anchor="w")
        self.category = tk.StringVar(value="Temperatures")
        group = ttk.Combobox(side, textvariable=self.category, values=("Temperatures", "Boost / pressure", "Speed", "RPM", "Voltage", "Other"), state="readonly", width=25)
        group.pack(fill="x", pady=6)
        group.bind("<<ComboboxSelected>>", lambda e: self.populate_signals())
        ttk.Label(side, text="Select up to 3 signals of the same units.", wraplength=230).pack(anchor="w", pady=(0,6))
        self.signal_list = tk.Listbox(side, selectmode="multiple", exportselection=False,
                                     bg=CARD, fg=TEXT, selectbackground="#355268", selectforeground=TEXT,
                                     highlightthickness=0, font=("Segoe UI",10), width=29, height=12)
        self.signal_list.pack(fill="both", expand=True)
        self.signal_list.bind("<<ListboxSelect>>", lambda e: self.redraw())
        chart_frame = ttk.Frame(body)
        chart_frame.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(chart_frame, bg=CARD, highlightthickness=0, height=310)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<Button-1>", self.click_seek)
        self.hover_text = tk.StringVar(value="Move over the graph to inspect a reading.")
        ttk.Label(self, textvariable=self.hover_text, wraplength=1050).pack(anchor="w", pady=8)
        self.summary = ttk.Treeview(self, columns=("low","avg","peak","count"), height=3)
        self.summary.heading("#0", text="Selected signal • current window")
        self.summary.column("#0", width=350)
        for name,label in (("low","Minimum"),("avg","Average"),("peak","Peak"),("count","Samples / omitted")):
            self.summary.heading(name, text=label)
            self.summary.column(name,width=130)
        self.summary.pack(fill="x", pady=6)
        ttk.Label(self, text="Lines show recorded values or bucket means; whiskers retain each bucket’s min/max. Summaries use original samples.\nMissing/suspect readings are omitted, gaps are not interpolated, and different channels were sampled at different times.", wraplength=1050).pack(anchor="w", pady=5)
        self.refresh()
        self.after(100,self.poll_load)
        self.after(100,self.play_tick)

    def refresh(self):
        current = self.record_choice.get()
        paths = sorted(logs_directory().glob("*/sensors.csv"), reverse=True)
        for path in self.paths:
            if path not in paths:
                paths.append(path)
        self.paths = paths
        self.names = [(read_json(p.parent / "session.json").get("name") or p.parent.name) + (" / " + p.name if p.name != "sensors.csv" else "") for p in paths]
        # Keep duplicate folder labels distinct for CSV imports from elsewhere.
        if len(set(self.names)) != len(self.names):
            self.names = [str(p) for p in paths]
        self.record_choice["values"] = self.names
        if current in self.names:
            self.record_choice.set(current)
        elif self.names:
            self.record_choice.current(0)

    def import_csv(self):
        path = filedialog.askopenfilename(title="Import Colorado sensor log", initialdir=str(logs_directory()), filetypes=(("CSV recordings","*.csv"),))
        if path:
            self.load(Path(path))

    def open_selected(self):
        index = self.record_choice.current()
        if 0 <= index < len(self.paths):
            self.load(self.paths[index])

    def load(self,path):
        if self.loading:
            return
        self.loading = True
        self.playing = False
        self.play_button.config(text="Play")
        self.info.set("Reading recording…")
        def run():
            try:
                self.results.put((load_recording(path),None))
            except Exception as exc:
                self.results.put((None,str(exc)))
        threading.Thread(target=run,daemon=True).start()

    def poll_load(self):
        try:
            record,error = self.results.get_nowait()
            self.loading = False
            if error:
                self.info.set("Import failed: " + error + (" • previous recording retained" if self.recording else ""))
            else:
                self.recording = record
                if record["path"] not in self.paths:
                    self.paths.append(record["path"])
                self.refresh()
                self.record_choice.current(self.paths.index(record["path"]))
                self.page = 0
                self.cursor.set(0)
                self.scrubber.config(to=max(.001, record["end"]-record["start"]))
                self.event_choice["values"] = [f"{elapsed(e['time']-record['start'])} • {e['label']}" for e in record["events"]]
                self.event_choice.set("")
                self.event_note.set(record["metadata"].get("notes", ""))
                self.populate_signals()
                self.update_cursor()
        except queue.Empty:
            pass
        self.after(100,self.poll_load)

    def edit_notes(self):
        if self.recording is None:
            return
        rec = self.recording
        window = tk.Toplevel(self)
        window.title("Recording details")
        frame = ttk.Frame(window, padding=18)
        frame.pack(fill="both", expand=True)
        name = tk.StringVar(value=rec["metadata"].get("name", ""))
        ttk.Label(frame, text="Name").pack(anchor="w")
        ttk.Entry(frame, textvariable=name, width=60).pack(fill="x", pady=6)
        ttk.Label(frame, text="Notes (saved in session.json; CSV remains unchanged)").pack(anchor="w")
        notes = tk.Text(frame, width=60, height=8)
        notes.insert("1.0", rec["metadata"].get("notes", ""))
        notes.pack(fill="both", pady=6)
        def save():
            path = rec["path"].parent / "session.json"
            metadata = read_json(path)
            metadata.update(name=name.get().strip()[:120], notes=notes.get("1.0", "end-1c")[:10000])
            try:
                write_json(path, metadata)
                rec["metadata"] = metadata
                self.event_note.set(metadata["notes"])
                self.refresh()
                self.redraw()
                window.destroy()
            except OSError as exc:
                self.event_note.set("Could not save notes: " + str(exc))
        ttk.Button(frame, text="Save", command=save).pack(anchor="e")

    def toggle_play(self):
        if self.recording is None or self.loading:
            return
        if self.cursor.get() >= self.recording["end"]-self.recording["start"]:
            self.cursor.set(0)
        self.playing = not self.playing
        self.play_clock = time.monotonic()
        self.play_button.config(text="Pause" if self.playing else "Play")

    def play_tick(self):
        now = time.monotonic()
        if self.playing and self.recording is not None:
            end = self.recording["end"]-self.recording["start"]
            self.cursor.set(min(end, self.cursor.get()+(now-self.play_clock)*float(self.play_speed.get().rstrip("×"))))
            self.seek()
            if self.cursor.get() >= end:
                self.playing = False
                self.play_button.config(text="Play")
        self.play_clock = now
        self.after(100, self.play_tick)

    def seek(self, _=None):
        if self.recording is None:
            return
        width = WINDOWS[self.window.get()]
        page = min(max(0, math.ceil((self.recording["end"]-self.recording["start"])/(width or 1))-1), int(self.cursor.get()/width)) if width else 0
        if page != self.page:
            self.page = page
            self.redraw()
        self.update_cursor()

    def jump_event(self, _=None):
        index = self.event_choice.current()
        if self.recording is not None and 0 <= index < len(self.recording["events"]):
            event = self.recording["events"][index]
            self.cursor.set(max(0, min(self.recording["end"]-self.recording["start"], event["time"]-self.recording["start"])))
            self.event_note.set(event.get("note", ""))
            self.seek()

    def click_seek(self, event):
        if self.recording is not None and getattr(self, "plot_series", None):
            x0,x1,_,_,low,span = self.plot_geometry
            if x0 <= event.x <= x1:
                self.cursor.set(low+(event.x-x0)/(x1-x0)*span-self.recording["start"])
                self.seek()

    def update_cursor(self):
        if self.recording is None:
            return
        rec = self.recording
        stamp = rec["start"] + self.cursor.get()
        self.cursor_label.set(elapsed(self.cursor.get()))
        chunks = []
        for name in ("Vehicle speed", "Engine speed", "Calculated boost / vacuum", "Coolant"):
            matches = [(key, samples) for key, samples in rec["series"].items() if key[0] == name]
            if not matches:
                continue
            # ECU/unit/PID splits stay separate, even when labels match.
            for key, samples in matches:
                sample = sample_at(samples, stamp, 15 if name == "Coolant" else 3)
                chunks.append(f"{name}: {sample[1]:.1f} {key[1]} ({stamp-sample[0]:.1f}s old)" if sample else name+": —")
        self.snapshot.set("RECORDED • " + " | ".join(chunks))
        self.canvas.delete("playhead")
        if getattr(self, "plot_series", None):
            x0,x1,y0,y1,low,span = self.plot_geometry
            if low <= stamp <= low+span:
                x = x0+(stamp-low)/max(.001,span)*(x1-x0)
                self.canvas.create_line(x,y0,x,y1,fill=TEXT,width=2,tags="playhead")

    def populate_signals(self):
        self.signal_list.delete(0,"end")
        self.keys=[]
        if self.recording is None:
            return
        def category(key):
            unit=key[1]
            return "Temperatures" if unit in ("°F","°C") else "Boost / pressure" if unit in ("psi","kPa") else "Speed" if unit in ("mph","km/h") else "RPM" if unit=="RPM" else "Voltage" if unit=="V" else "Other"
        self.keys=[k for k,ss in self.recording["series"].items() if category(k)==self.category.get() and any(p[1] is not None for p in ss)]
        for key in self.keys:
            self.signal_list.insert("end",key[0]+" ("+key[1]+")")
        # Default to two meaningful temperatures instead of duplicate coolant channels.
        selected=[i for i,k in enumerate(self.keys) if k[0] in ("Coolant","Intake air temperature")]
        for i in selected[:2] or ([0] if self.keys else []):
            self.signal_list.selection_set(i)
        self.redraw()

    def reset_page(self):
        self.page=0
        self.cursor.set(0)
        self.redraw()

    def move(self,delta):
        self.page+=delta
        if self.recording is not None:
            low, _, self.page, _ = window_bounds(self.recording["start"], self.recording["end"], WINDOWS[self.window.get()], self.page)
            self.cursor.set(low-self.recording["start"])
        self.redraw()

    def redraw(self):
        c=self.canvas
        c.delete("all")
        self.plot_series=[]
        for item in self.summary.get_children():
            self.summary.delete(item)
        w,h=max(400,c.winfo_width()),max(220,c.winfo_height())
        if self.recording is None:
            c.create_text(w/2,h/2,text="Load a recording to explore its graphs",fill=MUTED,font=("Segoe UI",13))
            self.prev.config(state="disabled"); self.next.config(state="disabled")
            return
        rec=self.recording
        low,high,self.page,pages=window_bounds(rec["start"],rec["end"],WINDOWS[self.window.get()],self.page)
        self.prev.config(state="normal" if self.page>0 else "disabled")
        self.next.config(state="normal" if self.page<pages-1 else "disabled")
        self.range_label.set(f"Window {self.page+1} of {pages}  •  {elapsed(low-rec['start'])} – {elapsed(high-rec['start'])} elapsed")
        meta=rec["metadata"]
        self.info.set(f"{meta.get('name') or rec['path'].parent.name}  |  {rec['started'].strftime('%b %d, %Y • %I:%M:%S %p %z')}  |  Duration {elapsed(rec['end']-rec['start'])}  |  Fuel: {meta.get('fuel_in_tank','not recorded')}  |  Profile: {meta.get('profile','not recorded')}  |  {rec['bad_rows']} unreadable rows / {rec['bad_events']} invalid markers skipped")
        selected=list(self.signal_list.curselection())[:3]
        keys=[self.keys[i] for i in selected]
        if not keys:
            c.create_text(w/2,h/2,text="Select a signal on the left",fill=MUTED)
            return
        if len({k[1] for k in keys})>1:
            c.create_text(w/2,h/2,text="Select signals with the same units to share an axis",fill=MUTED)
            return
        if len(self.signal_list.curselection())>3:
            self.hover_text.set("Showing the first 3 selected signals. Deselect a signal to compare another.")
        plots=[]
        for index,key in enumerate(keys):
            samples=select_samples(rec["series"][key],low,high,include_end=high==rec["end"])
            vv=[p[1] for p in samples if p[1] is not None]
            self.summary.insert("","end",text=key[0]+" • "+key[1],values=tuple(f"{n:.2f}" for n in (min(vv),statistics.mean(vv),max(vv)))+(f"{len(vv)} / {len(samples)-len(vv)}",) if vv else ("—","—","—",f"0 / {len(samples)}"))
            if not vv:
                continue
            points=aggregate(samples,DETAIL[self.detail.get()],rec["start"])
            # Bound canvas work for large imports, preserving extrema per bin.
            if len(points)>1500:
                points=aggregate(samples,max(.001,(high-low)/1000),low)
            original=rec["series"][key]
            gaps=[b[0]-a[0] for a,b in zip(original,original[1:]) if b[0]>a[0]]
            gap_limit=max(2,5*statistics.median(gaps)) if gaps else 2
            plots.append((key,points,COLORS[index],gap_limit,samples))
        if not plots:
            c.create_text(w/2,h/2,text="No usable samples in this window",fill=MUTED)
            return
        ymin=min(p[2] for _,pp,_,_,_ in plots for p in pp)
        ymax=max(p[3] for _,pp,_,_,_ in plots for p in pp)
        margin=max(.5,(ymax-ymin)*.12)
        ymin-=margin; ymax+=margin
        x0,x1,y0,y1=75,w-24,42,h-45
        span=max(.001,high-low)
        x=lambda t:x0+(t-low)/span*(x1-x0)
        y=lambda v:y1-(v-ymin)/(ymax-ymin)*(y1-y0)
        for i in range(6):
            yy=y0+(y1-y0)*i/5
            c.create_line(x0,yy,x1,yy,fill="#2b3b4b")
            c.create_text(x0-10,yy,text=f"{ymax-(ymax-ymin)*i/5:.1f}",anchor="e",fill=MUTED)
            xx=x0+(x1-x0)*i/5
            c.create_text(xx,y1+19,text=elapsed(low-rec["start"]+span*i/5),fill=MUTED)
        c.create_text(12,17,text=keys[0][1],anchor="w",fill=MUTED)
        for index,(key,points,color,gap_limit,samples) in enumerate(plots):
            c.create_text(x0+index*max(180,(x1-x0)/3),17,text=key[0],anchor="w",fill=color,width=max(170,(x1-x0)/3-8))
            previous=None
            missing_times=[ss[0] for ss in samples if ss[1] is None]
            for point in points:
                t,mean,minimum,maximum,count=point
                xx,yy=x(t),y(mean)
                if count>1:
                    c.create_line(xx,y(minimum),xx,y(maximum),fill=color,width=1)
                allowed=previous is not None and t-previous[0]<=max(gap_limit,DETAIL[self.detail.get()]*1.6)
                if allowed:
                    # Missing/flagged values explicitly break the line.
                    allowed=bisect.bisect_left(missing_times,t)==bisect.bisect_right(missing_times,previous[0])
                if allowed:
                    c.create_line(x(previous[0]),y(previous[1]),xx,yy,fill=color,width=2)
                c.create_oval(xx-2,yy-2,xx+2,yy+2,fill=color,outline="")
                previous=point
            self.plot_series.append((key,points,color))
        self.plot_geometry=(x0,x1,y0,y1,low,span)
        for event in rec["events"]:
            if low <= event["time"] <= high:
                xx=x(event["time"])
                c.create_line(xx,y0,xx,y1,fill="#ffbe68",dash=(2,5))
                c.create_text(xx,y0+12,text="◆",fill="#ffbe68")
        self.update_cursor()

    def hover(self,event):
        if not self.plot_series:
            return
        x0,x1,y0,y1,low,span=self.plot_geometry
        if not x0<=event.x<=x1:
            return
        stamp=low+(event.x-x0)/(x1-x0)*span
        self.canvas.delete("cursor")
        self.canvas.create_line(event.x,y0,event.x,y1,fill=MUTED,dash=(3,3),tags="cursor")
        chunks=[]
        for key,points,color in self.plot_series:
            p=min(points,key=lambda p:abs(p[0]-stamp))
            chunks.append(f"{key[0]}: {p[1]:.2f} {key[1]} @ {elapsed(p[0]-self.recording['start'])}" + (f" [min {p[2]:.2f}, max {p[3]:.2f}; n={p[4]}]" if p[4]>1 else ""))
        self.hover_text.set("  |  ".join(chunks))


def self_test():
    data=[(0,10,"ok"),(29,30,"ok"),(30,50,"ok"),(31,None,"bad"),(60,90,"ok")]
    p=aggregate(data,30,0)
    assert p[0][1:]==(20,10,30,2) and p[1][1]==50 and p[2][1]==90
    assert len(select_samples(data,0,30))==2
    assert len(select_samples(data,30,60,True))==3
    assert window_bounds(0,60,30,5)==(30,60,1,2)
    assert window_bounds(0,0,30,0)==(0,0,0,1)
    assert aggregate([(1,None,"unsupported")],60,0)==[]
    print("Log review aggregation, window boundaries, missing-value and peak-preservation checks passed.")
