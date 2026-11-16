import XCTest
@testable import ColoradoCore

final class OBDTests: XCTestCase {
    func testCommandAllowlist() throws {
        for command in OBD.allowed {
            XCTAssertEqual(try OBD.command(command), Data((command + "\r").utf8))
        }
        for command in ["04", "ATZ", "ATWR", "ATSH18DA18F1", "2701", "2E1234", "010C\r04", "010c", "010C "] {
            XCTAssertThrowsError(try OBD.command(command), command)
        }
    }

    func testECMReassemblyAndIsolation() throws {
        let capture = "18DAF11804410CFFFF\r18DAF1111017490A0145434D\r18DAF11121002D456E67696E\r18DAF1112265436F6E74726F\r18DAF111236C000000000000\r>"
        let p = try OBD.payload(capture)
        XCTAssertEqual(String(decoding: p.dropFirst(3).filter { $0 != 0 }, as: UTF8.self), "ECM-EngineControl")
        XCTAssertThrowsError(try OBD.payload(capture.replacingOccurrences(of: "112100", with: "112200")))
        for raw in ["NO DATA\r>", "18DAF11804410C1F40", "18DAF111037F0122", "18DAF11103410C00", "18DAF11104410C1F"] {
            XCTAssertThrowsError(try OBD.decode(pid: 0x0C, raw: raw), raw)
        }
    }

    func testSupportBitmapAndProfiles() throws {
        XCTAssertEqual(try OBD.supported(base: 0, payload: [0x41, 0, 0x80, 0, 0, 1]), [1, 32])
        XCTAssertThrowsError(try OBD.supported(base: 32, payload: [0x41, 0, 0x80, 0, 0, 1]))
        let support: Set<Int> = [0x0C, 0x10, 0x87, 0x67]
        XCTAssertEqual(OBD.intervals(support: support, performance: true), [0x0C: 0.2, 0x87: 0.2])
        XCTAssertEqual(OBD.intervals(support: support, performance: false), [0x0C: 0.5, 0x10: 2, 0x87: 0.5, 0x67: 3])
    }

    func testBoostPrefersExtendedAndExpires() {
        let now = Date(timeIntervalSince1970: 1000)
        let good = "Received / unvalidated"
        var readings = [0x33: Reading(pid: 0x33, value: 100, quality: good, time: now),
                        0x0B: Reading(pid: 0x0B, value: 140, quality: good, time: now),
                        0x8701: Reading(pid: 0x8701, value: 200, quality: good, time: now)]
        XCTAssertEqual(OBD.boost(readings, now: now)!, 100/6.894757, accuracy: 0.000001)
        readings[0x8701] = Reading(pid: 0x8701, value: 200, quality: "Suspect endpoint / verify", time: now)
        XCTAssertEqual(OBD.boost(readings, now: now)!, 40/6.894757, accuracy: 0.000001)
        XCTAssertNil(OBD.boost(readings, now: now.addingTimeInterval(4)))
        readings[0x0B] = Reading(pid: 0x0B, value: 255, quality: good, time: now)
        XCTAssertNil(OBD.boost(readings, now: now))
        readings[0x8701] = Reading(pid: 0x8701, value: 200, quality: good, time: now.addingTimeInterval(12))
        XCTAssertNil(OBD.boost(readings, now: now.addingTimeInterval(12)))
    }

    func testQualityDoesNotAcceptSuspectOrNonfiniteValues() {
        XCTAssertFalse(Reading(pid: 5, value: 0, quality: "Suspect endpoint / verify").usable)
        XCTAssertFalse(Reading(pid: 5, value: nil, quality: "Received / unvalidated").usable)
        XCTAssertFalse(Reading(pid: 5, value: .infinity, quality: "Received / unvalidated").usable)
        XCTAssertTrue(Reading(pid: 5, value: 104, quality: "Received / unvalidated").usable)
    }

    struct Fixture: Decodable {
        let pid: Int
        let raw: String
        let readings: [Expected]
        struct Expected: Decodable {
            let pid: Int
            let value: Double?
            let quality: String
        }
    }

    func testDesktopParityFixtures() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "desktop-fixtures", withExtension: "json"))
        let fixtures = try JSONDecoder().decode([Fixture].self, from: Data(contentsOf: url))
        XCTAssertGreaterThan(fixtures.count, 40)
        for fixture in fixtures {
            let readings = try OBD.decode(pid: fixture.pid, raw: fixture.raw)
            XCTAssertEqual(readings.count, fixture.readings.count)
            for (actual, expected) in zip(readings, fixture.readings) {
                XCTAssertEqual(actual.pid, expected.pid)
                XCTAssertEqual(actual.quality, expected.quality)
                if let value = expected.value {
                    XCTAssertEqual(try XCTUnwrap(actual.value), value, accuracy: 0.0000001)
                } else { XCTAssertNil(actual.value) }
            }
        }
    }
}
