import Foundation

public enum OBDError: LocalizedError {
    case invalid(String)
    public var errorDescription: String? {
        switch self { case .invalid(let message): return message }
    }
}

public struct Sensor: Identifiable, Sendable {
    public let id: Int
    public let name: String
    public let unit: String
    public let interval: Double

    public static let all: [Sensor] = [
        .init(id: 0x0C, name: "Engine speed", unit: "RPM", interval: 0.5),
        .init(id: 0x0D, name: "Vehicle speed", unit: "mph", interval: 0.5),
        .init(id: 0x05, name: "Coolant", unit: "°F", interval: 2),
        .init(id: 0x42, name: "ECM voltage", unit: "V", interval: 2),
        .init(id: 0x0B, name: "Manifold absolute pressure", unit: "kPa", interval: 1),
        .init(id: 0x33, name: "Barometric pressure", unit: "kPa", interval: 5),
        .init(id: 0x11, name: "Absolute throttle", unit: "%", interval: 1),
        .init(id: 0x04, name: "Calculated load", unit: "%", interval: 1),
        .init(id: 0x0F, name: "Intake air temperature", unit: "°F", interval: 2),
        .init(id: 0x10, name: "Mass airflow", unit: "g/s", interval: 2),
        .init(id: 0x0E, name: "Ignition advance", unit: "deg", interval: 2),
        .init(id: 0x06, name: "Short fuel trim B1", unit: "%", interval: 2),
        .init(id: 0x07, name: "Long fuel trim B1", unit: "%", interval: 5),
        .init(id: 0x2F, name: "Fuel level", unit: "%", interval: 5),
        .init(id: 0x8701, name: "Extended manifold pressure A", unit: "kPa", interval: 0.5),
        .init(id: 0x8702, name: "Extended manifold pressure B", unit: "kPa", interval: 0.5)
    ] + (1...2).map {
        Sensor(id: 0x6700 + $0, name: "Extended coolant sensor \($0)", unit: "°F", interval: 3)
    } + (1...6).map {
        Sensor(id: 0x6800 + $0, name: "Intake temperature B\(1 + ($0-1)/3) S\(1 + ($0-1)%3)", unit: "°F", interval: 3)
    }
}

public struct Reading: Sendable {
    public let pid: Int
    public let value: Double?
    public let quality: String
    public let time: Date

    public init(pid: Int, value: Double?, quality: String, time: Date = Date()) {
        self.pid = pid; self.value = value; self.quality = quality; self.time = time
    }

    public var usable: Bool {
        value?.isFinite == true && (quality == "Received / unvalidated" || quality.hasPrefix("Derived from asynchronous"))
    }
}

public enum OBD {
    public static let adapter = ["ATI", "ATDP", "ATDPN"]
    public static let setup = ["ATH1", "ATS1", "ATCAF1", "ATCFC1", "ATSH18DA11F1", "ATCRA18DAF111"]
    public static let discovery = stride(from: 0, through: 0xE0, by: 0x20).map { String(format: "01%02X", $0) }
    public static let allowed = Set(adapter + setup + discovery + ["090A", "0167", "0168", "0187"] +
        Sensor.all.filter { $0.id < 256 }.map { String(format: "01%02X", $0.id) })

    public static func command(_ text: String) throws -> Data {
        guard allowed.contains(text) else { throw OBDError.invalid("Command is outside the read-only allowlist") }
        return Data((text + "\r").utf8)
    }

    /// Reassemble only ECM 11 frames, including ISO-TP sequence validation.
    public static func payload(_ raw: String) throws -> [UInt8] {
        var buffer: [UInt8] = []
        var length: Int?
        var sequence = 1
        for line in raw.components(separatedBy: .newlines) {
            let compact = line.filter { !$0.isWhitespace && $0 != ">" }.uppercased()
            guard compact.hasPrefix("18DAF111") else { continue }
            let hex = Array(compact.dropFirst(8))
            guard !hex.isEmpty, hex.count.isMultiple(of: 2) else { continue }
            var bytes: [UInt8] = []
            for i in stride(from: 0, to: hex.count, by: 2) {
                guard let byte = UInt8(String(hex[i...i+1]), radix: 16) else {
                    throw OBDError.invalid("Malformed ECM frame")
                }
                bytes.append(byte)
            }
            switch bytes[0] >> 4 {
            case 0:
                let size = Int(bytes[0] & 15)
                if size > 0, bytes.count >= size + 1 { return Array(bytes[1...size]) }
            case 1 where bytes.count >= 2:
                length = (Int(bytes[0] & 15) << 8) | Int(bytes[1])
                buffer = Array(bytes.dropFirst(2)); sequence = 1
            case 2:
                guard let size = length else { continue }
                guard Int(bytes[0] & 15) == sequence else { throw OBDError.invalid("Out-of-order ECM reply") }
                buffer += bytes.dropFirst(); sequence = (sequence + 1) % 16
                if buffer.count >= size { return Array(buffer.prefix(size)) }
            default: continue
            }
        }
        throw OBDError.invalid("No complete ECM reply")
    }

    public static func supported(base: Int, payload p: [UInt8]) throws -> Set<Int> {
        guard p.count == 6, p[0] == 0x41, Int(p[1]) == base else {
            throw OBDError.invalid("Invalid supported-PID response")
        }
        let bits = p.dropFirst(2).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        return Set((1...32).filter { bits & (UInt32(1) << (32-$0)) != 0 }.map { base + $0 })
    }

    public static func decode(pid: Int, raw: String, time: Date = Date()) throws -> [Reading] {
        let p = try payload(raw)
        guard p.count >= 2, p[0] == 0x41, Int(p[1]) == pid else {
            throw OBDError.invalid("Rejected request or unexpected PID")
        }
        let b = Array(p.dropFirst(2))
        let received = "Received / unvalidated", suspect = "Suspect endpoint / verify"
        if [0x67, 0x68, 0x87].contains(pid) {
            let count = pid == 0x68 ? 6 : 2
            let expected = pid == 0x87 ? 5 : count + 1
            guard b.count == expected else { throw OBDError.invalid("Unexpected extended PID length") }
            return (0..<count).map { index in
                let channel = (pid << 8) + index + 1
                guard b[0] & (1 << index) != 0 else {
                    return Reading(pid: channel, value: nil, quality: "Sensor not supported by response flags", time: time)
                }
                if pid == 0x87 {
                    let word = Int(b[1 + index*2]) * 256 + Int(b[2 + index*2])
                    return Reading(pid: channel, value: Double(word)/32, quality: word == 0 || word == 65535 ? suspect : received, time: time)
                }
                let byte = b[index+1]
                return Reading(pid: channel, value: (Double(byte)-40)*1.8+32, quality: byte == 0 ? suspect : received, time: time)
            }
        }
        let expected = [0x0C, 0x42, 0x10].contains(pid) ? 2 : 1
        guard Sensor.all.contains(where: { $0.id == pid }), b.count == expected else {
            throw OBDError.invalid("Unexpected PID or length")
        }
        let a = Double(b[0])
        let word = b.count == 2 ? a*256 + Double(b[1]) : a
        let value: Double
        switch pid {
        case 0x0C: value = word/4
        case 0x0D: value = a/1.609344
        case 0x05, 0x0F: value = (a-40)*1.8+32
        case 0x42: value = word/1000
        case 0x11, 0x04, 0x2F: value = a*100/255
        case 0x10: value = word/100
        case 0x0E: value = a/2-64
        case 0x06, 0x07: value = (a-128)*100/128
        default: value = a
        }
        let flagged = ([0x05, 0x0F, 0x06].contains(pid) && a == 0) || (pid == 0x33 && !(20...120).contains(value))
        return [Reading(pid: pid, value: value, quality: flagged ? suspect : received, time: time)]
    }

    public static func intervals(support: Set<Int>, performance: Bool) -> [Int: Double] {
        let fast: [Int: Double] = [0x0C: 0.2, 0x0D: 0.2, 0x0E: 0.2, 0x87: 0.2, 0x0B: 0.2,
                                 0x11: 0.5, 0x04: 0.5, 0x0F: 2, 0x05: 5, 0x42: 5, 0x33: 5]
        var result: [Int: Double] = [:]
        for sensor in Sensor.all {
            let pid = sensor.id < 256 ? sensor.id : sensor.id >> 8
            guard support.contains(pid) else { continue }
            if performance { result[pid] = fast[pid] }
            else { result[pid] = sensor.interval }
        }
        return result
    }

    public static func boost(_ readings: [Int: Reading], now: Date = Date()) -> Double? {
        guard let baro = readings[0x33], baro.usable, let b = baro.value,
              (20...120).contains(b), (0..<12).contains(now.timeIntervalSince(baro.time)) else { return nil }
        for pid in [0x8701, 0x0B] {
            if let map = readings[pid], map.usable, let a = map.value, a > 0,
               (0..<4).contains(now.timeIntervalSince(map.time)), pid != 0x0B || a < 255 {
                return (a-b)/6.894757
            }
        }
        return nil
    }
}
