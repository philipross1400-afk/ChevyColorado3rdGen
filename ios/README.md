# Colorado Live for iOS · development version

Native SwiftUI live dashboard for iPhone and iPad (iOS 17+), designed for a **direct OBDLink MX+ connection** through Apple's External Accessory framework. No laptop relay or server is used. Windows v0.7 uses matching presets and a portable recording format.

This is an initial implementation, **not a verified working MX+ release**. The Linux development environment cannot compile Apple's frameworks, sign the app, run the iOS simulator, or test the adapter. Do not infer hardware compatibility from the presence of transport code.

## Manufacturer integration dependency

MX+ is an iOS-compatible Bluetooth adapter, but the Windows COM-port implementation cannot be used on iOS. External Accessory requires a manufacturer-defined accessory protocol registered in the application's Info.plist. No protocol identifier is guessed or bundled here.

Before live use, obtain from OBDLink / OBD Solutions:

1. The supported MX+ iOS accessory protocol identifier and integration documentation.
2. Confirmation of any required app/bundle-ID authorization, SDK, MFi accessory app listing, or distribution steps for this app.
3. Confirmation that the exposed session supports the ASCII command/prompt exchange used here, or documentation for any required framing/handshake.

If their integration requires a different stream format, adapt `AccessoryTransport` before vehicle testing. Merely adding an arbitrary protocol string does not establish compatibility. The Connect button stays disabled until a protocol has been configured; opening also requires an exact match to the paired accessory's advertised protocols.

References: [OBDLink MX+](https://www.obdlink.com/products/obdlink-mxp/), [Apple External Accessory](https://developer.apple.com/documentation/externalaccessory), [supported accessory protocols](https://developer.apple.com/documentation/bundleresources/information-property-list/uisupportedexternalaccessoryprotocols), [Apple MFi FAQ](https://mfi.apple.com/faqs).

## Build on a Mac

Install Xcode 15+ and XcodeGen (`brew install xcodegen`), then:

```sh
cd ios
xcodegen generate
open ColoradoLive.xcodeproj
```

In Xcode select the ColoradoLive target, choose your signing team and a unique bundle identifier, and set the **OBDLINK_ACCESSORY_PROTOCOL** build setting to the identifier supplied by OBDLink. Use the same bundle ID for any manufacturer registration. Generated projects and plist files are ignored; `project.yml` is the source of project configuration. To persist your approved configuration, update the corresponding build settings there locally.

The UI runs without an adapter, with empty gauges and a connection setup message. Live use requires a physical iPhone/iPad; pair MX+ in Settings → Bluetooth, close other OBD apps, open Colorado Live, confirm parked setup, refresh accessories, and connect. Only one adapter owner/app can use the connection at a time. No automatic reconnection is implemented.

## Implemented scope

- Read-only finite command allowlist, protocol-7 check, ECM identity check, standard PID support discovery, sequential Performance/Overview polling, eight-second exchange timeout, and reply-size limit.
- Sensor conversions and extended PID 67/68/87 channels ported from the desktop source; extended MAP preferred for asynchronous boost calculation.
- Saved gauge layouts, four matching desktop presets, larger cards, and a sensor list with quality/age handling. Suspect, missing, stale, and disconnected values are not shown as live readings.
- Local `sensors.csv`, `session.json`, and `raw.jsonl` recordings under Documents/dashboard_logs. Share CSV after stopping; retrieve the full session folder through Files/Finder file sharing. No cloud upload.
- CSV columns match the desktop log viewer. App backgrounding, user stop, transport failures, and log-write failures end polling. Background acquisition is intentionally absent in this first version.

- Recordings library with name/notes search, folder/CSV import, synchronized playback at 0.5×–4×, same-unit signal graphs with bounded min/max buckets, event navigation, metadata editing, and CSV/session-folder sharing.
- Recording names, fuel grade, notes and live timestamped event markers. Vehicle capabilities show saved ECM discovery plus current channel quality.

Import the full session folder in the Recordings tab to preserve `session.json` and `events.jsonl`. Standalone CSV import preserves samples only. Imports are copied into the app library; editing details does not change the original imported CSV. See [the shared format](../docs/recording-format.md).

Not yet ported: TCM identity discovery. Existing desktop log review can open the exported sensor CSV, including derived boost rows. Oil pressure, knock, transmission temperature, gear, and slip remain unavailable. Readings are unvalidated and not a diagnosis.

## Verification

On a Mac (the core package can also run with a Linux Swift 5.9+ toolchain):

```sh
cd ios/ColoradoCore
swift test
```

From the repository root, compile the complete app without signing:

```sh
cd ios
xcodegen generate
xcodebuild -project ColoradoLive.xcodeproj -scheme ColoradoLive \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

Hardware validation still needed: manufacturer authorization/session opening; protocol/ECM discovery; comparison against desktop values; extended responses and unsupported sensors; partial writes/fragmented replies; unplug/timeouts; stop/backgrounding; file sharing and CSV import into desktop review. Do this while parked, parking brake applied, outdoors if idling.
