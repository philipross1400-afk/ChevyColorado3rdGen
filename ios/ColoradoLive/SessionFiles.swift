import Foundation
import Combine
import ColoradoCore

enum SessionFiles {
    static func root() throws -> URL {
        let documents = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let root = documents.appendingPathComponent("dashboard_logs")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    static func updateMetadata(_ folder: URL, values: [String: Any]) throws {
        let url = folder.appendingPathComponent("session.json")
        var metadata = (try? JSONSerialization.jsonObject(with: Data(contentsOf: url))) as? [String: Any] ?? [:]
        metadata.merge(values) { _, new in new }
        try JSONSerialization.data(withJSONObject: metadata, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
    }

    /// Validate first, then copy into the app library. Folder import preserves annotation sidecars.
    static func importRecording(_ source: URL) throws -> URL {
        let recording = try Recording.load(source)
        let destination = try root().appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
        do {
            try FileManager.default.copyItem(at: recording.url, to: destination.appendingPathComponent("sensors.csv"))
            var directory: ObjCBool = false
            FileManager.default.fileExists(atPath: source.path, isDirectory: &directory)
            if directory.boolValue {
                for name in ["session.json", "events.jsonl"] {
                    let file = source.appendingPathComponent(name)
                    if FileManager.default.fileExists(atPath: file.path) {
                        try FileManager.default.copyItem(at: file, to: destination.appendingPathComponent(name))
                    }
                }
            }
            return destination
        } catch {
            try? FileManager.default.removeItem(at: destination)
            throw error
        }
    }
}

struct LibraryEntry: Identifiable {
    let folder: URL
    let name: String
    let notes: String
    var id: String { folder.path }
}

@MainActor
final class RecordingLibrary: ObservableObject {
    @Published var entries: [LibraryEntry] = []
    @Published var selected: Recording?
    @Published var busy = false
    @Published var error: String?

    func refresh(excluding active: URL?) {
        do {
            entries = try FileManager.default.contentsOfDirectory(at: SessionFiles.root(), includingPropertiesForKeys: nil)
                .filter { $0 != active && FileManager.default.fileExists(atPath: $0.appendingPathComponent("sensors.csv").path) }
                .map { folder in
                    let metadata = (try? JSONSerialization.jsonObject(with: Data(contentsOf: folder.appendingPathComponent("session.json")))) as? [String: Any] ?? [:]
                    let name = metadata["name"] as? String ?? ""
                    return LibraryEntry(folder: folder, name: name.isEmpty ? folder.lastPathComponent : name, notes: metadata["notes"] as? String ?? "")
                }.sorted { $0.folder.lastPathComponent > $1.folder.lastPathComponent }
        } catch { self.error = error.localizedDescription }
    }

    func open(_ url: URL, importing: Bool = false) {
        guard !busy else { return }
        busy = true
        Task {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() }; busy = false }
            do {
                selected = try await Task.detached(priority: .userInitiated) {
                    let location = importing ? try SessionFiles.importRecording(url) : url
                    return try Recording.load(location)
                }.value
            } catch { self.error = error.localizedDescription }
        }
    }
}
