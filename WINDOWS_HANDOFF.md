# Windows development handoff

Repository: https://github.com/philipross1400-afk/ChevyColorado3rdGen

Active branch: **windows/refine-monitor**. Base: main. iOS is paused in draft PR #1; this branch has no iOS source changes or build dependencies. Continue desktop work here rather than the earlier combined `ios/colorado-live` branch. Preserve existing local edits when switching branches.

## Implemented milestone

- Saved dashboard layouts: one to four gauges, left-to-right ordering, hidden slots, larger cards, four display presets. Choosing a display preset does not change the polling profile.
- Named recordings, existing fuel/session notes, timestamped live event markers with an optional note.
- Recorded-data playback: timeline seeking, 0.5×/1×/2×/4× speeds, synchronized speed/RPM/boost/coolant snapshots, graph cursor and event jumps. No future samples, interpolation, or carrying values through invalid readings.
- Capability page: ECM advertised support, profile polling, per-channel sample quality, explicitly unverified definitions, and a discovery snapshot in session.json.
- Offline name/notes editing that preserves existing metadata and the sensor CSV. Scrollable Live/Review pages for shorter displays.

## Run locally

Use Python 3.10+ with tkinter; Python 3.12 is recommended. From the repository folder in PowerShell:

```powershell
py -m pip install -r requirements.txt
py colorado_dashboard.py
```

The Windows application uses Tkinter, not .NET: no Visual Studio solution or C# workload is required. Open the folder in your editor, then use the terminal or configure `colorado_dashboard.py` as the Python startup file.

## Validate before hardware testing

```powershell
py -m unittest discover -s tests -v
py colorado_readonly.py --self-test
py colorado_dashboard.py --self-test
```

All these checks are offline and send no vehicle commands. The Tk interface regression creates temporary synthetic recordings and exercises layouts, capability results, notes, marker jumps and playback. Local live acquisition with the changed UI still needs testing on the user's PC and adapter. Set up while parked; the app retains the existing parked-setup gate and read-only request allowlist.

## Continue with local Codex

Inspect this branch and read README.md plus docs/recording-format.md. Run the tests and launch the Windows app offline. Refine desktop usability with the user before adding more features. Preserve dashboard_logs, personal recordings and dashboard_settings.json; do not commit them. Keep iOS frozen. Enhanced transmission/oil/knock/fuel-pressure signals remain research targets and must not be guessed or displayed as verified.

Future ideas outside this milestone: rolling pre-event capture, session-to-session comparison, configurable sustained alerts, verified enhanced channels, read-only DTC/freeze-frame/readiness reports, and broader recording-library management.
