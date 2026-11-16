import SwiftUI
import ExternalAccessory
import ColoradoCore

@MainActor
final class LiveModel: ObservableObject {
    @Published var accessories: [EAAccessory] = []
    @Published var readings: [Int: Reading] = [:]
    @Published var status = "Disconnected · no queries sent"
    @Published var running = false
    @Published var performance = true
    @Published var parked = false
    @Published var csvURL: URL?
    @Published var errorMessage: String?
    @Published var signalStatus: [Int: String] = [:]
    @Published var capabilities: CapabilitySnapshot?
    @Published var recordingName = ""
    @Published var recordingNotes = ""
    @Published var fuel = "Unknown"
    @Published var markerLabel = "Observation"
    @Published var markerNote = ""
    @Published var markerCount = 0
    @Published var layout: DashboardLayout {
        didSet {
            if let data = try? JSONEncoder().encode(layout) { UserDefaults.standard.set(data, forKey: "dashboard_layout_v1") }
        }
    }
    private let transport = AccessoryTransport()
    private var task: Task<Void, Never>?
    private var csv: FileHandle?
    private var rawLog: FileHandle?
    private var eventLog: FileHandle?
    private var notifications: [NSObjectProtocol] = []

    init() {
        if let data = UserDefaults.standard.data(forKey: "dashboard_layout_v1"),
           let saved = try? JSONDecoder().decode(DashboardLayout.self, from: data) {
            layout = DashboardLayout(signals: saved.signals, largeCards: saved.large_cards)
        } else { layout = DashboardLayout() }
        let manager = EAAccessoryManager.shared()
        manager.registerForLocalNotifications()
        for name in [Notification.Name.EAAccessoryDidConnect, .EAAccessoryDidDisconnect] {
            notifications.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.refreshAccessories() }
            })
        }
        refreshAccessories()
    }

    func refreshAccessories() {
        accessories = EAAccessoryManager.shared().connectedAccessories
            .filter { $0.name.localizedCaseInsensitiveContains("OBDLink MX+") }
    }

    func start(_ accessory: EAAccessory) {
        guard parked, !running else { return }
        running = true; readings = [:]; signalStatus = [:]; csvURL = nil; errorMessage = nil
        capabilities = nil; markerCount = 0
        status = "Connecting to \(accessory.name)…"
        task = Task {
            do {
                try transport.open(accessory)
                try beginRecording()
                for command in OBD.adapter {
                    let reply = try await ask(command)
                    if command == "ATDPN" {
                        let tokens = reply.uppercased().split { $0.isWhitespace || $0 == ">" }.map(String.init)
                        guard tokens.contains("7") || tokens.contains("A7") else {
                            throw OBDError.invalid("Protocol 7 not confirmed. No vehicle queries sent.")
                        }
                    }
                }
                for command in OBD.setup {
                    let reply = try await ask(command)
                    guard reply.split(whereSeparator: { $0.isWhitespace || $0 == ">" }).contains("OK") else {
                        throw OBDError.invalid("Adapter did not acknowledge \(command)")
                    }
                }
                let identity = try OBD.payload(await ask("090A"))
                guard identity.starts(with: [0x49, 0x0A, 0x01]),
                      String(decoding: identity.dropFirst(3), as: UTF8.self).uppercased().contains("ECM") else {
                    throw OBDError.invalid("ECM identity not confirmed")
                }
                var support: Set<Int> = []
                for base in stride(from: 0, through: 0xE0, by: 0x20) {
                    let raw = try await ask(String(format: "01%02X", base))
                    let bits = try OBD.supported(base: base, payload: OBD.payload(raw))
                    support.formUnion(bits)
                    if !bits.contains(base + 32) { break }
                }
                let intervals = OBD.intervals(support: support, performance: performance)
                guard !intervals.isEmpty else { throw OBDError.invalid("No implemented sensors advertised by ECM") }
                let active = Sensor.all.filter { intervals[$0.id < 256 ? $0.id : $0.id >> 8] != nil }.map(\.id)
                let snapshot = CapabilitySnapshot(name: String(decoding: identity.dropFirst(3).filter { $0 != 0 }, as: UTF8.self),
                                                  supported: support.sorted(), active: active, profile: performance ? "Performance" : "Overview")
                capabilities = snapshot
                if let folder = csvURL?.deletingLastPathComponent() {
                    try SessionFiles.updateMetadata(folder, values: ["capabilities": JSONSerialization.jsonObject(with: JSONEncoder().encode(snapshot))])
                }
                for sensor in Sensor.all {
                    let source = sensor.id < 256 ? sensor.id : sensor.id >> 8
                    signalStatus[sensor.id] = intervals[source] != nil ? "Waiting" :
                        support.contains(source) ? "Not polled in this profile" : "Not advertised by ECM"
                }
                status = "Connected · recording read-only ECM data"
                var due = intervals.mapValues { _ in ProcessInfo.processInfo.systemUptime }
                while !Task.isCancelled {
                    guard let pid = due.min(by: { $0.value < $1.value })?.key else { break }
                    try await pause(max(0, due[pid]! - ProcessInfo.processInfo.systemUptime))
                    let started = ProcessInfo.processInfo.systemUptime
                    let raw = try await ask(String(format: "01%02X", pid))
                    let samples: [Reading]
                    do { samples = try OBD.decode(pid: pid, raw: raw) }
                    catch {
                        let channels = Sensor.all.filter { $0.id == pid || $0.id >> 8 == pid }
                        samples = channels.map { Reading(pid: $0.id, value: nil, quality: error.localizedDescription) }
                    }
                    for sample in samples { try record(sample) }
                    due[pid] = ProcessInfo.processInfo.systemUptime + intervals[pid]!
                    try await pause(max(0.03, 0.1 - (ProcessInfo.processInfo.systemUptime-started)))
                }
                status = "Stopped · recording saved"
            } catch is CancellationError {
                status = "Stopped · recording saved"
            } catch {
                status = "Stopped · \(error.localizedDescription)"
                errorMessage = error.localizedDescription
            }
            transport.close()
            closeRecording()
            readings = [:] // Never leave disconnected readings looking live.
            signalStatus = signalStatus.mapValues { _ in "Stopped · see saved recording" }
            running = false; task = nil
        }
    }

    func stop() {
        guard running else { return }
        task?.cancel()
        transport.close()
    }

    func markEvent() {
        guard running, let eventLog else { return }
        let event = SessionMarker(label: markerLabel, note: markerNote)
        guard !event.label.isEmpty else { errorMessage = "Enter an event label"; return }
        do {
            var data = try JSONEncoder().encode(event)
            data.append(10)
            try eventLog.write(contentsOf: data)
            markerCount += 1; markerNote = ""
        } catch {
            errorMessage = "Could not save event: \(error.localizedDescription)"
            stop()
        }
    }

    private func pause(_ seconds: Double) async throws {
        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    private func ask(_ command: String) async throws -> String {
        try Task.checkCancellation()
        let raw = try await transport.exchange(command)
        let event: [String: String] = ["time": timestamp(Date()), "command": command, "raw": raw]
        var data = try JSONSerialization.data(withJSONObject: event, options: [.sortedKeys])
        data.append(10)
        try rawLog?.write(contentsOf: data)
        if ["CAN ERROR", "BUS ERROR", "BUS INIT", "STOPPED", "UNABLE TO CONNECT"].contains(where: raw.uppercased().contains) {
            throw OBDError.invalid("Adapter reported a bus/connection error; stopped")
        }
        return raw
    }

    private func beginRecording() throws {
        let documents = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let folder = documents.appendingPathComponent("dashboard_logs").appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent("sensors.csv")
        try Data("\u{FEFF}timestamp,ecu,pid,sensor,value,units,quality\r\n".utf8).write(to: url)
        csv = try FileHandle(forWritingTo: url); try csv?.seekToEnd()
        let rawURL = folder.appendingPathComponent("raw.jsonl")
        try Data().write(to: rawURL)
        rawLog = try FileHandle(forWritingTo: rawURL)
        let eventURL = folder.appendingPathComponent("events.jsonl")
        try Data().write(to: eventURL)
        eventLog = try FileHandle(forWritingTo: eventURL)
        let metadata: [String: Any] = ["schema_version": 1, "app_version": "ios-0.2", "started": timestamp(Date()),
            "name": recordingName, "notes": recordingNotes, "fuel_in_tank": fuel,
            "profile": performance ? "Performance" : "Overview", "transport": "ExternalAccessory", "scope": "ECM only; no TCM discovery"]
        try JSONSerialization.data(withJSONObject: metadata, options: [.prettyPrinted, .sortedKeys])
            .write(to: folder.appendingPathComponent("session.json"))
        csvURL = url
    }

    private func record(_ sample: Reading) throws {
        guard let sensor = Sensor.all.first(where: { $0.id == sample.pid }) else { return }
        readings[sample.pid] = sample
        signalStatus[sample.pid] = sample.quality
        let pidLabel = sample.pid < 256 ? String(format: "01%02X", sample.pid) :
            String(format: "01%02X/sensor%d", sample.pid >> 8, sample.pid & 255)
        try writeRow([timestamp(sample.time), "18DAF111", pidLabel, sensor.name,
                      sample.value.map { String($0) } ?? "", sensor.unit, sample.quality])
        if [0x8701, 0x0B, 0x33].contains(sample.pid), let boost = OBD.boost(readings, now: sample.time) {
            let extended = readings[0x8701]
            let useExtended = extended?.usable == true && extended?.value.map { $0 > 0 } == true &&
                extended.map { (0..<4).contains(sample.time.timeIntervalSince($0.time)) } == true
            let source = useExtended ? 0x8701 : 0x0B
            try writeRow([timestamp(sample.time), "18DAF111", "derived", "Calculated boost / vacuum", String(boost), "psi",
                          String(format: "Derived from asynchronous MAP %04X and barometric pressure; unvalidated", source)])
        }
    }

    private func timestamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    private func writeRow(_ row: [String]) throws {
        let line = row.map { "\"" + $0.replacingOccurrences(of: "\"", with: "\"\"") + "\"" }.joined(separator: ",") + "\r\n"
        try csv?.write(contentsOf: Data(line.utf8))
    }

    private func closeRecording() {
        for handle in [csv, rawLog, eventLog].compactMap({ $0 }) {
            do { try handle.synchronize(); try handle.close() }
            catch { errorMessage = "Recording close failed: \(error.localizedDescription)" }
        }
        csv = nil; rawLog = nil; eventLog = nil
    }
}
