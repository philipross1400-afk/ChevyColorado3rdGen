# Chevy Colorado Dashboard v0.7

Native iPhone/iPad source is in [ios/](ios/README.md), with matching dashboard presets, recording annotations, capabilities and offline playback. The unsigned iOS Simulator build is checked in macOS CI. Direct MX+ integration still requires manufacturer configuration, signing and physical-device verification. No Apple enrollment or distribution is needed to use the Windows app.

A Windows Python dashboard and offline recording viewer developed with a 2023 Chevrolet Colorado and OBDLink MX+.

## Run

Install Python 3.10 or newer with tkinter (Python 3.12 recommended on Windows), then run:

```console
python -m pip install -r requirements.txt
python colorado_dashboard.py
```

Alternatively double-click `Start Colorado Dashboard.cmd` after installing the dependency.

The Live Dashboard uses the adapter's outgoing Bluetooth port (default COM3). Close other OBD applications first. Set up while parked; a passenger operates the laptop while moving, or record unattended and review after parking. Idle outdoors only.

## Dashboard layouts and event markers

Use **Customize dashboard…** to select one to four gauges in left-to-right order, hide slots, choose larger cards, or apply Everyday, Performance, Towing and Temperatures presets. Your layout is saved locally. These presets only change the display; Performance/Overview still controls polling.

Set a recording name, fuel grade and session notes before connecting. While recording, enter an event label and optional note, then choose **Mark event**. Event markers capture the current time without sending extra vehicle commands. Setup and event marking are for a passenger or while parked.

The **Vehicle Capabilities** tab separates advertised PID support, current polling and actual sample quality. Extended PID support does not prove that every channel exists. The discovery snapshot is saved with each recording. Oil/knock/transmission definitions remain explicitly unverified.

## Review recordings

Log Review works without a vehicle connection. Import a dashboard `sensors.csv`, select a whole recording or 30-second, 1-minute, or 5-minute window, and graph up to three signals with matching units. Graph detail supports original samples or interval averages with min/max ranges. Scrub the timeline or play at 0.5×, 1×, 2× or 4× speed. Recorded speed, RPM, boost and coolant snapshots follow the cursor; click the graph or select an event marker to jump to that moment. Playback never uses future samples, interpolates missing data, or presents stale values as current. Use **Edit name / notes…** to update the sidecar metadata without changing the sensor CSV.

This release supports this dashboard's CSV format, not arbitrary third-party logs. Transfer the whole session folder, including `session.json` and `events.jsonl`, to retain notes and markers between platforms. See the [portable recording format](docs/recording-format.md).

Recordings are saved beside the app in `dashboard_logs`. Personal recordings are excluded from this repository.

## Read-only scope and limitations

All transmitted commands pass through the explicit allowlist in `colorado_readonly.exchange`; the dashboard defines its finite allowed requests at startup. No programming, security unlock, code clearing, adaptation reset, or actuator control is implemented. This is not a universal vehicle scanner.

ECM readings use the verified 29-bit request address 18DA11F1. Optional TCM discovery uses 18DA18F1 and reads identity and standard supported-PID information, then restores the ECM address. It does not provide live transmission slip, gear, or fluid temperature. Oil pressure and knock retard have no verified definitions and are not implemented.

Boost is calculated from valid manifold absolute pressure minus barometric pressure; samples are asynchronous. Values and derived measurements are not independently calibrated and should not be treated as a diagnosis.

## Offline checks

These commands do not open the adapter:

```console
python colorado_readonly.py --self-test
python colorado_dashboard.py --self-test
python -c "import colorado_log_viewer as v; v.self_test()"
python -m unittest discover -s tests -v
```

Earlier acquisition versions were used for actual recordings. v0.6 added offline log review; v0.7 adds layouts, annotations, playback and capability snapshots; packaged EXE execution was blocked by the development environment's Application Control policy and remains unverified there. Do not disable security protections to run the EXE. Python source is available for inspection and use.

## Optional Windows EXE build

On Windows with Python and tkinter:

```console
python -m pip install -r requirements.txt pyinstaller==6.22.3
python -m PyInstaller --noconfirm --onefile --windowed --name ColoradoDashboard colorado_dashboard.py
```

The unsigned executable is created in `dist`. Include applicable Python, Tcl/Tk, pySerial, and PyInstaller notices when distributing a binary. Keep personal logs out of release packages. The application does not require cloud upload.
