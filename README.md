# Chevy Colorado Dashboard v0.6

A Windows Python dashboard and offline recording viewer developed with a 2023 Chevrolet Colorado and OBDLink MX+.

## Run

Install Python with tkinter, then run:

```console
python -m pip install -r requirements.txt
python colorado_dashboard.py
```

Alternatively double-click `Start Colorado Dashboard.cmd` after installing the dependency.

The Live Dashboard uses the adapter's outgoing Bluetooth port (default COM3). Close other OBD applications first. Set up while parked; a passenger operates the laptop while moving, or record unattended and review after parking. Idle outdoors only.

## Review recordings

Log Review works without a vehicle connection. Import a dashboard `sensors.csv`, select a whole recording or 30-second, 1-minute, or 5-minute window, and graph up to three signals with matching units. Graph detail supports original samples or interval averages with min/max ranges. This release supports this dashboard's CSV format, not arbitrary third-party logs.

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
```

Earlier acquisition versions were used for actual recordings. v0.6 added offline log review; packaged EXE execution was blocked by the development environment's Application Control policy and remains unverified there. Do not disable security protections to run the EXE. Python source is available for inspection and use.

## Optional Windows EXE build

On Windows with Python and tkinter:

```console
python -m pip install -r requirements.txt pyinstaller==6.22.3
python -m PyInstaller --noconfirm --onefile --windowed --name ColoradoDashboard colorado_dashboard.py
```

The unsigned executable is created in `dist`. Include applicable Python, Tcl/Tk, pySerial, and PyInstaller notices when distributing a binary. Keep personal logs out of release packages. The application does not require cloud upload.
