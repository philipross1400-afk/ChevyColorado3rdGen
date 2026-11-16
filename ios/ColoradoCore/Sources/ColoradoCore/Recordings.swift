@preconcurrency import Foundation

public enum RecordingFormat {
    public static func timestamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    public static func date(_ text: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text)
    }
}

public struct SessionMarker: Codable, Identifiable, Sendable {
    public var schema_version = 1
    public let id: String
    public let timestamp: String
    public let label: String
    public let note: String
    public var date: Date? { RecordingFormat.date(timestamp) }

    public init(label: String, note: String = "", date: Date = Date()) {
        self.id = UUID().uuidString
        self.timestamp = RecordingFormat.timestamp(date)
        self.label = String(label.trimmingCharacters(in: .whitespacesAndNewlines).prefix(120))
        self.note = String(note.prefix(2000))
    }
}

public struct CapabilitySnapshot: Codable, Sendable {
    public let name: String
    public let supported: [Int]
    public let active: [Int]
    public let profile: String

    public init(name: String, supported: [Int], active: [Int], profile: String) {
        self.name = name; self.supported = supported; self.active = active; self.profile = profile
    }

    public func status(pid: Int) -> String {
        let source = pid < 256 ? pid : pid >> 8
        if active.contains(pid) { return pid < 256 ? "Polled" : "Parent PID polled · check channel flags" }
        return supported.contains(source) ? "Advertised · not polled in this profile" : "Not advertised by ECM"
    }

    public static let unverified = ["Oil pressure", "Oil temperature", "Knock retard", "Transmission temperature",
                                    "Commanded / actual gear", "Torque-converter slip", "Requested boost", "Fuel-rail pressure"]
}

public struct DashboardLayout: Codable, Equatable, Sendable {
    public var schema_version = 1
    public var signals: [String]
    public var large_cards: Bool
    public static let presets: [(String, [String])] = [
        ("Everyday", ["000D", "000C", "boost", "0005"]),
        ("Performance", ["000C", "boost", "0011", "000E"]),
        ("Towing", ["0005", "0004", "boost", "000F"]),
        ("Temperatures", ["0005", "000F", "6701", "6801"])
    ]
    public init(signals: [String] = presets[0].1, largeCards: Bool = false) {
        var seen = Set<String>()
        let valid = signals.filter { GaugeSignal.find($0) != nil && seen.insert($0).inserted }
        self.signals = valid.isEmpty ? Self.presets[0].1 : Array(valid.prefix(4))
        self.large_cards = largeCards
    }
}

public struct GaugeSignal: Identifiable, Sendable {
    public let id: String
    public let name: String
    public let unit: String
    public let range: ClosedRange<Double>
    public let pid: Int?
    public static var all: [GaugeSignal] {
        Sensor.all.map { sensor in
            let range: ClosedRange<Double>
            switch sensor.unit {
            case "RPM": range = 0...6000
            case "mph": range = 0...120
            case "°F": range = 0...300
            case "kPa": range = 0...400
            case "%": range = 0...100
            case "V": range = 8...18
            case "deg": range = -30...60
            case "g/s": range = 0...300
            default: range = 0...100
            }
            return GaugeSignal(id: String(format: "%04X", sensor.id), name: sensor.name, unit: sensor.unit, range: range, pid: sensor.id)
        } + [GaugeSignal(id: "boost", name: "Boost / vacuum", unit: "psi", range: -15...30, pid: nil)]
    }
    public static func find(_ id: String) -> GaugeSignal? { all.first { $0.id == id } }
}

public struct RecordedSample: Sendable {
    public let time: Date
    public let value: Double?
    public let quality: String
}

public struct RecordedSignal: Identifiable, Sendable {
    public let id: String
    public let name: String
    public let unit: String
    public let ecu: String
    public let pid: String
    public var samples: [RecordedSample]

    /// No future samples, interpolation, or carrying a value through a missing/flagged sample.
    public func sample(at time: Date, maxAge: Double = 3) -> RecordedSample? {
        var low = 0, high = samples.count
        while low < high {
            let middle = (low+high)/2
            if samples[middle].time <= time { low = middle+1 } else { high = middle }
        }
        guard low > 0 else { return nil }
        let sample = samples[low-1]
        guard sample.value != nil, time.timeIntervalSince(sample.time) <= maxAge else { return nil }
        return sample
    }

    public var gapLimit: Double {
        let gaps = zip(samples, samples.dropFirst()).map { $1.time.timeIntervalSince($0.time) }.filter { $0 > 0 }.sorted()
        return gaps.isEmpty ? 2 : max(2, gaps[gaps.count/2]*5)
    }
}

public struct Recording: Sendable {
    public let url: URL
    public let start: Date
    public let end: Date
    public let signals: [RecordedSignal]
    public let markers: [SessionMarker]
    public let name: String
    public let notes: String
    public let fuel: String
    public let profile: String
    public let capabilities: CapabilitySnapshot?
    public let badRows: Int
    public let badMarkers: Int

    public static func load(_ location: URL) throws -> Recording {
        var isDirectory: ObjCBool = false
        _ = FileManager.default.fileExists(atPath: location.path, isDirectory: &isDirectory)
        let url = isDirectory.boolValue ? location.appendingPathComponent("sensors.csv") : location
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard (attributes[.size] as? NSNumber)?.intValue ?? 0 <= 64*1024*1024 else {
            throw OBDError.invalid("Recording exceeds the 64 MB import limit")
        }
        let text = try String(contentsOf: url, encoding: .utf8)
        var header: [String]?
        var groups: [String: RecordedSignal] = [:]
        var first: Date?, last: Date?
        var total = 0, bad = 0
        let fraction = ISO8601DateFormatter()
        fraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let whole = ISO8601DateFormatter()
        try CSVRows.visit(text) { row in
            if header == nil {
                guard Set(["timestamp", "sensor", "value", "units", "quality"]).isSubset(of: Set(row)) else {
                    throw OBDError.invalid("Choose a Colorado sensors.csv file")
                }
                guard Set(row).count == row.count else { throw OBDError.invalid("Duplicate CSV columns") }
                header = row; return
            }
            total += 1
            guard total <= 500000 else { throw OBDError.invalid("Recording exceeds the 500,000-row import limit") }
            guard row.count == header!.count else { bad += 1; return }
            let fields = Dictionary(uniqueKeysWithValues: zip(header!, row))
            guard let dateText = fields["timestamp"], let time = fraction.date(from: dateText) ?? whole.date(from: dateText),
                  let rawName = fields["sensor"], !rawName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { bad += 1; return }
            let name = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
            let unit = (fields["units"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let ecu = fields["ecu"] ?? "", pid = fields["pid"] ?? ""
            let quality = fields["quality"].flatMap { $0.isEmpty ? nil : $0 } ?? "Unknown quality"
            let valueText = fields["value"] ?? ""
            let number = Double(valueText)
            guard valueText.isEmpty || (number?.isFinite == true) else { bad += 1; return }
            let usable = quality == "Received / unvalidated" || quality.hasPrefix("Derived from asynchronous")
            let id = [name, unit, ecu, pid].map { "\($0.utf8.count):\($0)" }.joined()
            if groups[id] == nil { groups[id] = RecordedSignal(id: id, name: name, unit: unit, ecu: ecu, pid: pid, samples: []) }
            groups[id]!.samples.append(RecordedSample(time: time, value: usable ? number : nil, quality: quality))
            first = min(first ?? time, time); last = max(last ?? time, time)
        }
        guard let start = first, let end = last, !groups.isEmpty else { throw OBDError.invalid("No readable timestamped samples found") }
        let folder = url.deletingLastPathComponent()
        let metadataData = try? Data(contentsOf: folder.appendingPathComponent("session.json"))
        let metadata = metadataData.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        let capabilities = (metadata["capabilities"] as? [String: Any]).flatMap {
            try? JSONDecoder().decode(CapabilitySnapshot.self, from: JSONSerialization.data(withJSONObject: $0))
        }
        var markers: [SessionMarker] = [], badMarkers = 0
        if let events = try? String(contentsOf: folder.appendingPathComponent("events.jsonl"), encoding: .utf8) {
            for (index, line) in events.split(whereSeparator: \.isNewline).enumerated() {
                guard index < 10000 else { badMarkers += 1; break }
                if let marker = try? JSONDecoder().decode(SessionMarker.self, from: Data(line.utf8)), marker.date != nil,
                   !marker.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { markers.append(marker) }
                else { badMarkers += 1 }
            }
        }
        var signals = Array(groups.values)
        for i in signals.indices { signals[i].samples.sort { $0.time < $1.time } }
        signals.sort { ($0.name, $0.id) < ($1.name, $1.id) }
        return Recording(url: url, start: start, end: end, signals: signals,
                         markers: markers.sorted { $0.date! < $1.date! }, name: metadata["name"] as? String ?? "",
                         notes: metadata["notes"] as? String ?? "", fuel: metadata["fuel_in_tank"] as? String ?? "Unknown",
                         profile: metadata["profile"] as? String ?? "Unknown", capabilities: capabilities, badRows: bad, badMarkers: badMarkers)
    }
}

/// UTF-8 CSV with BOM, escaped quotes, quoted newlines and CRLF. Rows are consumed incrementally.
public enum CSVRows {
    public static func visit(_ text: String, row consume: ([String]) throws -> Void) throws {
        var field = "", fields: [String] = []
        var quoted = false, closedQuote = false, began = false
        var characters = text.drop(while: { $0 == "\u{FEFF}" }).makeIterator()
        while let character = characters.next() {
            if quoted {
                if character == "\"" { quoted = false; closedQuote = true }
                else { field.append(character) }
            } else if character == "\"" {
                if closedQuote { field.append("\""); quoted = true; closedQuote = false }
                else if !began { quoted = true }
                else { throw OBDError.invalid("Malformed CSV quoting") }
                began = true
            } else if character == "," {
                fields.append(field); field = ""; began = false; closedQuote = false
            } else if character.isNewline {
                fields.append(field)
                if fields != [""] { try consume(fields) }
                fields = []; field = ""; began = false; closedQuote = false
            } else {
                guard !closedQuote else { throw OBDError.invalid("Unexpected text after a CSV quote") }
                field.append(character); began = true
            }
        }
        guard !quoted else { throw OBDError.invalid("Unterminated CSV quote") }
        if began || !field.isEmpty || !fields.isEmpty { fields.append(field); try consume(fields) }
    }
}
