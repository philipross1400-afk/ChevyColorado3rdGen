# Portable recordings · schema version 1

Windows v0.7 uses this portable session folder format. The paused iOS prototype uses the same annotation schema. Transfer the **whole folder** to preserve annotations and discovery. A standalone CSV imports samples only; missing sidecars are allowed. No original sensor CSV is modified during review.

## Files

- `sensors.csv`: UTF-8, optional BOM, CSV quoting. Required columns: `timestamp,sensor,value,units,quality`. Current writers also include `ecu,pid`. Timestamps must include a timezone. Samples remain asynchronous. Same-name signals with different units, ECU or PID are kept separate.
- `session.json`: JSON object. `schema_version: 1`, `name`, `notes`, `started`, `app_version`, `profile`, `fuel_in_tank`. Additional platform/session fields are permitted and retained when editing details. Old desktop metadata without `schema_version` is supported.
- `events.jsonl`: one JSON object per marker: `schema_version: 1`, UUID `id`, timezone-aware `timestamp`, nonempty `label` (up to 120 characters), `note` (up to 2000 characters). Malformed markers are skipped and counted; valid markers are sorted by time. Markers may fall outside the available sensor range; seeking clamps to the recorded data range.
- `raw.jsonl`: platform-specific acquisition trace; never required for playback. Windows records acquisition events, while iOS records ASCII command/reply pairs. These are not interchangeable raw-replay formats.

`session.json.capabilities`, when present, has:

```json
{
  "name": "ECM-EngineControl",
  "supported": [5, 12, 135],
  "active": [5, 12, 34561, 34562],
  "profile": "Performance"
}
```

PID IDs are decimal JSON numbers; extended channel IDs are `(parentPID << 8) + channel`. Discovery only proves parent PID support. Extended-channel response flags and quality determine whether individual values are available. The Windows snapshot may contain extra acquisition fields. No vehicle identity, VIN, model year or software version is inferred from the ECU name.

## Quality and playback

`Received / unvalidated` and qualities beginning `Derived from asynchronous` are eligible numeric samples. Other qualities retain a timestamp but display no numeric value. Nonfinite numbers, invalid timestamps and malformed rows are skipped. Import limits are 64 MB and 500,000 sensor rows; at most 10,000 event lines are read.

Playback selects the last recorded sample **at or before** the cursor, never a future/nearest sample. Missing or suspect samples break continuity. Playback gauge cards expire after three seconds, or fifteen seconds for coolant (slower polling). Charts are separately bounded for rendering and retain bucket min/max; chart lines break at invalid samples and long gaps. No inferred transmission/oil/knock values are generated.

## Dashboard preferences

The same four preset signal IDs/order are used on both platforms. IDs are four-digit uppercase hexadecimal channel IDs (`000C`, `6701`) plus `boost` for the existing derived pressure value. Preferences contain `schema_version: 1`, `signals` (one to four unique IDs), and `large_cards` (boolean). Windows saves `dashboard_settings.json` beside the app; iOS stores the JSON in UserDefaults. Layout preferences do not change the acquisition profile or enable vehicle queries.

The desktop tests use `tests/fixtures/portable-session.json`. It contains out-of-order timestamps, quoted multiline fields, invalid data and Unicode annotations; it contains no personal recordings. This Windows branch has no dependency on the iOS source or test suite.
