import Foundation
import ExternalAccessory
import ColoradoCore

/// All stream access stays on the main run loop. Exactly one command may be pending.
@MainActor
final class AccessoryTransport: NSObject, StreamDelegate {
    private var session: EASession?
    private var pending: CheckedContinuation<String, Error>?
    private var timeout: Task<Void, Never>?
    private var response = Data()
    private var outgoing = Data()
    private var offset = 0

    static var configuredProtocols: [String] {
        (Bundle.main.object(forInfoDictionaryKey: "UISupportedExternalAccessoryProtocols") as? [String] ?? [])
            .filter { !$0.isEmpty && !$0.contains("$(") }
    }

    func open(_ accessory: EAAccessory) throws {
        close()
        guard let protocolName = accessory.protocolStrings.first(where: Self.configuredProtocols.contains) else {
            throw OBDError.invalid("No configured MX+ protocol matches this accessory. Obtain the protocol and app requirements from OBDLink; see ios/README.md.")
        }
        guard let session = EASession(accessory: accessory, forProtocol: protocolName),
              let input = session.inputStream, let output = session.outputStream else {
            throw OBDError.invalid("iOS could not open the accessory session. Check pairing and manufacturer app authorization.")
        }
        self.session = session
        for stream in [input as Stream, output as Stream] {
            stream.delegate = self
            stream.schedule(in: .main, forMode: .common)
            stream.open()
        }
    }

    func exchange(_ command: String) async throws -> String {
        let data = try OBD.command(command)
        guard session != nil, pending == nil else { throw OBDError.invalid("Accessory is unavailable or busy") }
        try Task.checkCancellation()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                pending = continuation
                response.removeAll(keepingCapacity: true)
                outgoing = data; offset = 0
                timeout = Task { [weak self] in
                    do { try await Task.sleep(nanoseconds: 8_000_000_000) }
                    catch { return }
                    self?.close(error: OBDError.invalid("No complete adapter prompt within 8 seconds; stopped"))
                }
                writeAvailable()
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.close(error: CancellationError()) }
        }
    }

    private func writeAvailable() {
        guard let output = session?.outputStream, pending != nil else { return }
        while output.hasSpaceAvailable && offset < outgoing.count {
            let written = outgoing.withUnsafeBytes { bytes -> Int in
                guard let pointer = bytes.baseAddress?.assumingMemoryBound(to: UInt8.self) else { return 0 }
                return output.write(pointer.advanced(by: offset), maxLength: outgoing.count-offset)
            }
            if written < 0 { close(error: output.streamError ?? OBDError.invalid("Accessory write failed")); return }
            if written == 0 { break }
            offset += written
        }
    }

    // Foundation calls the delegate on the main run loop selected in open().
    nonisolated func stream(_ aStream: Stream, handle eventCode: Stream.Event) {
        MainActor.assumeIsolated { handle(aStream, event: eventCode) }
    }

    private func handle(_ stream: Stream, event: Stream.Event) {
        switch event {
        case .hasSpaceAvailable: writeAvailable()
        case .hasBytesAvailable:
            guard let input = stream as? InputStream else { return }
            var bytes = [UInt8](repeating: 0, count: 1024)
            while input.hasBytesAvailable {
                let count = input.read(&bytes, maxLength: bytes.count)
                if count < 0 { close(error: input.streamError ?? OBDError.invalid("Accessory read failed")); return }
                if count == 0 { break }
                guard pending != nil else { continue }
                response.append(contentsOf: bytes.prefix(count))
                if response.count > 32768 { close(error: OBDError.invalid("Oversized adapter reply; stopped")); return }
                if response.contains(62) {
                    let reply = String(decoding: response, as: UTF8.self)
                    let continuation = pending; pending = nil
                    timeout?.cancel(); timeout = nil
                    continuation?.resume(returning: reply)
                }
            }
        case .errorOccurred, .endEncountered:
            close(error: stream.streamError ?? OBDError.invalid("Accessory disconnected"))
        default: break
        }
    }

    func close(error: Error = CancellationError()) {
        timeout?.cancel(); timeout = nil
        let continuation = pending; pending = nil
        if let session {
            for stream in [session.inputStream as Stream?, session.outputStream as Stream?].compactMap({ $0 }) {
                stream.close(); stream.remove(from: .main, forMode: .common); stream.delegate = nil
            }
        }
        session = nil; response.removeAll(); outgoing.removeAll(); offset = 0
        continuation?.resume(throwing: error)
    }
}
