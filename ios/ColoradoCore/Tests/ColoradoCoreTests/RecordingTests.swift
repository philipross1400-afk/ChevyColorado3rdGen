import XCTest
@testable import ColoradoCore

final class RecordingTests: XCTestCase {
    struct Fixture: Decodable {
        let csv: String
        let metadata: Metadata
        let events: String
        struct Metadata: Codable {
            let schema_version: Int
            let name: String
            let notes: String
            let fuel_in_tank: String
            let profile: String
            let capabilities: CapabilitySnapshot
        }
    }

    func testPortableSessionMatchesPython() throws {
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: XCTUnwrap(Bundle.module.url(forResource: "session-fixture", withExtension: "json"))))
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        try Data(fixture.csv.utf8).write(to: folder.appendingPathComponent("sensors.csv"))
        try JSONEncoder().encode(fixture.metadata).write(to: folder.appendingPathComponent("session.json"))
        try Data(fixture.events.utf8).write(to: folder.appendingPathComponent("events.jsonl"))
        let recording = try Recording.load(folder)
        XCTAssertEqual(recording.signals.count, 3)
        XCTAssertEqual(recording.badRows, 2)
        XCTAssertEqual(recording.badMarkers, 1)
        XCTAssertEqual(recording.name, "Portable ✓ session")
        XCTAssertEqual(recording.end.timeIntervalSince(recording.start), 4)
        XCTAssertEqual(recording.markers.first?.label, "Shift ✓")
        XCTAssertEqual(recording.markers.first?.note, "Passenger marker, with \"quotes\"")
        XCTAssertEqual(recording.notes, "Sample notes\nSecond line")
        let rpm = try XCTUnwrap(recording.signals.first { $0.name == "Engine speed" })
        XCTAssertNil(rpm.sample(at: recording.start.addingTimeInterval(-1)))
        XCTAssertEqual(rpm.sample(at: recording.start.addingTimeInterval(1.9))?.value, 1000)
        XCTAssertNil(rpm.sample(at: recording.start.addingTimeInterval(2.5)))
        XCTAssertEqual(rpm.sample(at: recording.end)?.value, 2000)
        XCTAssertNil(rpm.sample(at: recording.end.addingTimeInterval(3.1)))
        XCTAssertEqual(recording.capabilities?.status(pid: 0x8701), "Parent PID polled · check channel flags")
        XCTAssertEqual(recording.capabilities?.status(pid: 0x10), "Not advertised by ECM")
    }

    func testCSVQuotingBOMAndErrors() throws {
        var rows: [[String]] = []
        try CSVRows.visit("\u{FEFF}a,b\r\n\"line1\nline2\",\"quoted \"\"text\"\"\"\r\n") { rows.append($0) }
        XCTAssertEqual(rows, [["a", "b"], ["line1\nline2", "quoted \"text\""]])
        for text in ["\"unfinished", "a\"b,c", "\"a\"garbage,b"] {
            XCTAssertThrowsError(try CSVRows.visit(text) { _ in })
        }
    }

    func testMissingSidecarsAndECUUnitSeparation() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let url = folder.appendingPathComponent("sensors.csv")
        let text = "timestamp,ecu,pid,sensor,value,units,quality\n" +
            "2026-01-01T00:00:00Z,11,0105,Temperature,100,°F,Received / unvalidated\n" +
            "2026-01-01T00:00:00Z,18,0105,Temperature,100,°F,Received / unvalidated\n" +
            "2026-01-01T00:00:00Z,11,0105,Temperature,100,°C,Received / unvalidated\n"
        try Data(text.utf8).write(to: url)
        let recording = try Recording.load(url)
        XCTAssertEqual(recording.signals.count, 3)
        XCTAssertTrue(recording.markers.isEmpty)
        XCTAssertNil(recording.capabilities)
        XCTAssertEqual(recording.end, recording.start)
        try Data("timestamp,sensor,value,units,quality,value\n".utf8).write(to: url)
        XCTAssertThrowsError(try Recording.load(url))
    }

    func testLayoutsAndMarkerRoundTrip() throws {
        for (_, signals) in DashboardLayout.presets { XCTAssertTrue(signals.allSatisfy { GaugeSignal.find($0) != nil }) }
        XCTAssertEqual(DashboardLayout(signals: ["boost", "boost", "unknown"]).signals, ["boost"])
        let event = SessionMarker(label: " Shift ✓ ", note: "Passenger observation", date: Date(timeIntervalSince1970: 1000.25))
        let decoded = try JSONDecoder().decode(SessionMarker.self, from: JSONEncoder().encode(event))
        XCTAssertEqual(decoded.label, "Shift ✓")
        XCTAssertEqual(decoded.date!.timeIntervalSince1970, 1000.25, accuracy: 0.001)
    }
}
