import SwiftUI
import Charts
import UniformTypeIdentifiers
import ColoradoCore

struct RecordingLibraryView: View {
    @ObservedObject var live: LiveModel
    @StateObject private var library = RecordingLibrary()
    @State private var importing = false
    @State private var search = ""
    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button("Import recording folder or CSV", systemImage: "square.and.arrow.down") { importing = true }
                    Text("Import a folder to include session notes, events and capabilities. A CSV by itself contains sensor samples only.")
                        .font(.footnote).foregroundStyle(.secondary)
                    if library.busy { ProgressView("Reading recording…") }
                }
                Section("Saved recordings") {
                    ForEach(library.entries.filter { search.isEmpty || ($0.name + " " + $0.notes).localizedCaseInsensitiveContains(search) }) { entry in
                        Button { library.open(entry.folder) } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(entry.name).foregroundStyle(.primary)
                                if !entry.notes.isEmpty { Text(entry.notes).font(.caption).lineLimit(2).foregroundStyle(.secondary) }
                            }
                        }.disabled(library.busy)
                    }
                    if library.entries.isEmpty { Text("Record a session or import a desktop recording to start reviewing.").foregroundStyle(.secondary) }
                }
            }
            .navigationTitle("Recordings")
            .searchable(text: $search, prompt: "Names or notes")
            .toolbar { Button("Refresh", systemImage: "arrow.clockwise", action: refresh) }
            .onAppear(perform: refresh)
            .onChange(of: live.running) { _, _ in refresh() }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.folder, .commaSeparatedText], allowsMultipleSelection: false) { result in
                switch result {
                case .success(let urls): if let url = urls.first { library.open(url, importing: true) }
                case .failure(let error): library.error = error.localizedDescription
                }
            }
            .sheet(isPresented: Binding(get: { library.selected != nil }, set: { if !$0 { library.selected = nil; refresh() } })) {
                if let recording = library.selected { RecordingReview(recording: recording) }
            }
            .alert("Recording issue", isPresented: Binding(get: { library.error != nil }, set: { if !$0 { library.error = nil } })) {
                Button("OK") { library.error = nil }
            } message: { Text(library.error ?? "") }
        }
    }
    private func refresh() { library.refresh(excluding: live.running ? live.csvURL?.deletingLastPathComponent() : nil) }
}

private struct ReviewPoint: Identifiable {
    let id: Int
    let elapsed: Double
    let mean: Double
    let minimum: Double
    let maximum: Double
    let signal: String
    let segment: String
}

struct RecordingReview: View {
    let recording: Recording
    @Environment(\.dismiss) private var dismiss
    @State private var cursor = 0.0
    @State private var playing = false
    @State private var speed = 1.0
    @State private var lastTick = Date()
    @State private var window = 60.0
    @State private var selected: Set<String> = []
    @State private var points: [ReviewPoint] = []
    @State private var name = ""
    @State private var notes = ""
    @State private var error: String?
    private let timer = Timer.publish(every: 0.1, on: .main, in: .common).autoconnect()
    private var duration: Double { recording.end.timeIntervalSince(recording.start) }
    private var stamp: Date { recording.start.addingTimeInterval(cursor) }
    private var selectedSignals: [RecordedSignal] { recording.signals.filter { selected.contains($0.id) } }
    private var page: Int { window == 0 ? 0 : min(max(0, Int(ceil(duration/window))-1), Int(cursor/window)) }
    private var bounds: ClosedRange<Double> {
        let low = window == 0 ? 0 : Double(page)*window
        return low...max(low+0.001, window == 0 ? duration : min(duration, low+window))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("RECORDED DATA · \(format(cursor)) / \(format(duration))").font(.headline)
                    Text("\(recording.profile) · Fuel: \(recording.fuel) · \(recording.badRows) unreadable rows / \(recording.badMarkers) invalid markers skipped")
                        .font(.caption).foregroundStyle(.secondary)
                    playback
                    snapshot
                    chart
                    signals
                    markers
                    details
                    NavigationLink("Recorded vehicle capabilities") { CapabilityView(snapshot: recording.capabilities) }
                    ShareLink(item: recording.url) { Label("Share sensor CSV", systemImage: "square.and.arrow.up") }
                    ShareLink(item: recording.url.deletingLastPathComponent()) { Label("Share session folder", systemImage: "folder") }
                    Text("Samples are asynchronous and unvalidated. Playback uses the last usable recorded sample at or before the cursor, with a freshness limit. It never invents values between samples. Chart averages retain each bucket’s min/max; invalid samples and long gaps break lines.")
                        .font(.footnote).foregroundStyle(.secondary)
                }.padding()
            }
            .navigationTitle(name.isEmpty ? "Recording review" : name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { playing = false; dismiss() } }
            .onAppear {
                name = recording.name; notes = recording.notes
                let defaults = recording.signals.filter { $0.unit == "°F" }.prefix(2)
                selected = Set((defaults.isEmpty ? Array(recording.signals.prefix(1)) : Array(defaults)).map(\.id))
                rebuildPlot()
            }
            .onDisappear { playing = false }
            .onChange(of: selected) { _, _ in rebuildPlot() }
            .onChange(of: window) { _, _ in rebuildPlot() }
            .onChange(of: page) { _, _ in rebuildPlot() }
            .onReceive(timer) { now in
                defer { lastTick = now }
                if playing {
                    cursor = min(duration, cursor+max(0, now.timeIntervalSince(lastTick))*speed)
                    if cursor >= duration { playing = false }
                }
            }
            .alert("Could not save details", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
                Button("OK") { error = nil }
            } message: { Text(error ?? "") }
        }
    }

    private var playback: some View {
        VStack {
            HStack {
                Button(playing ? "Pause" : "Play", systemImage: playing ? "pause.fill" : "play.fill") {
                    if cursor >= duration { cursor = 0 }
                    lastTick = Date(); playing.toggle()
                }.disabled(duration == 0)
                Picker("Speed", selection: $speed) {
                    ForEach([0.5, 1, 2, 4], id: \.self) { Text("\($0, specifier: "%g")×").tag($0) }
                }.pickerStyle(.menu)
                Picker("Window", selection: $window) {
                    Text("All").tag(0.0); Text("30s").tag(30.0); Text("1m").tag(60.0); Text("5m").tag(300.0)
                }.pickerStyle(.menu)
            }
            Slider(value: $cursor, in: 0...max(0.001, duration)).disabled(duration == 0)
        }
    }

    private var snapshot: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())]) {
            ForEach(recording.signals.filter { ["Vehicle speed", "Engine speed", "Calculated boost / vacuum", "Coolant"].contains($0.name) }) { signal in
                let sample = signal.sample(at: stamp, maxAge: signal.name == "Coolant" ? 15 : 3)
                VStack(alignment: .leading, spacing: 4) {
                    Text(signal.name).font(.caption).foregroundStyle(.secondary)
                    Text(sample?.value.map { String(format: "%.1f %@", $0, signal.unit) } ?? "—").font(.title3.monospacedDigit())
                    Text(sample.map { String(format: "%.1fs old", stamp.timeIntervalSince($0.time)) } ?? "Missing / stale").font(.caption2)
                }.frame(maxWidth: .infinity, alignment: .leading).padding().background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    private var chart: some View {
        Chart {
            ForEach(points) { point in
                RuleMark(x: .value("Elapsed", point.elapsed), yStart: .value("Minimum", point.minimum), yEnd: .value("Maximum", point.maximum))
                    .foregroundStyle(by: .value("Signal", point.signal)).opacity(0.4)
                LineMark(x: .value("Elapsed", point.elapsed), y: .value("Value", point.mean), series: .value("Segment", point.segment))
                    .foregroundStyle(by: .value("Signal", point.signal))
                PointMark(x: .value("Elapsed", point.elapsed), y: .value("Value", point.mean)).symbolSize(8)
                    .foregroundStyle(by: .value("Signal", point.signal))
            }
            ForEach(recording.markers.filter { marker in marker.date.map { bounds.contains($0.timeIntervalSince(recording.start)) } == true }) { marker in
                RuleMark(x: .value("Event", marker.date!.timeIntervalSince(recording.start))).foregroundStyle(.orange)
            }
            RuleMark(x: .value("Cursor", cursor)).foregroundStyle(.white)
        }
        .chartXScale(domain: bounds)
        .chartXAxisLabel("Elapsed seconds")
        .chartYAxisLabel(selectedSignals.first?.unit ?? "")
        .frame(height: 250)
        .overlay { if points.isEmpty { Text("Select signals with usable samples in this window").foregroundStyle(.secondary) } }
    }

    private var signals: some View {
        VStack(alignment: .leading) {
            Text("Compare up to three signals with matching units").font(.headline)
            ForEach(recording.signals) { signal in
                Toggle(isOn: Binding(get: { selected.contains(signal.id) }, set: { enabled in
                    if enabled { selected.insert(signal.id) } else { selected.remove(signal.id) }
                })) {
                    VStack(alignment: .leading) {
                        Text("\(signal.name) (\(signal.unit))")
                        Text("\(signal.ecu) · \(signal.pid)").font(.caption2).foregroundStyle(.secondary)
                    }
                }.disabled(!selected.contains(signal.id) && (selected.count >= 3 || selectedSignals.first.map { $0.unit != signal.unit } == true))
            }
        }
    }

    private var markers: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Event markers").font(.headline)
            if recording.markers.isEmpty { Text("No event markers in this session").foregroundStyle(.secondary) }
            ForEach(recording.markers) { marker in
                Button {
                    if let date = marker.date { cursor = min(duration, max(0, date.timeIntervalSince(recording.start))) }
                } label: {
                    VStack(alignment: .leading) {
                        Text("\(format(marker.date!.timeIntervalSince(recording.start))) · \(marker.label)")
                        if !marker.note.isEmpty { Text(marker.note).font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
        }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Recording details").font(.headline)
            TextField("Name", text: $name)
            TextField("Notes", text: $notes, axis: .vertical)
            Button("Save details") {
                do { try SessionFiles.updateMetadata(recording.url.deletingLastPathComponent(), values: ["name": String(name.prefix(120)), "notes": String(notes.prefix(10000))]) }
                catch { self.error = error.localizedDescription }
            }
        }.textFieldStyle(.roundedBorder)
    }

    private func rebuildPlot() {
        var result: [ReviewPoint] = []
        for signal in selectedSignals {
            let gap = signal.gapLimit
            let width = max(0.001, (bounds.upperBound-bounds.lowerBound)/500)
            var buckets: [Int: (sum: Double, time: Double, min: Double, max: Double, count: Int, segment: Int)] = [:]
            var segment = 0
            var previous: Date?
            for sample in signal.samples {
                let elapsed = sample.time.timeIntervalSince(recording.start)
                guard bounds.contains(elapsed) else { continue }
                guard let value = sample.value else { segment += 1; previous = nil; continue }
                if let previous, sample.time.timeIntervalSince(previous) > gap { segment += 1 }
                previous = sample.time
                let bin = Int(floor((elapsed-bounds.lowerBound)/width))
                if var bucket = buckets[bin] {
                    bucket.sum += value; bucket.time += elapsed; bucket.min = min(bucket.min,value); bucket.max = max(bucket.max,value); bucket.count += 1
                    if bucket.segment != segment { bucket.segment = -bin-1 }
                    buckets[bin] = bucket
                } else { buckets[bin] = (value, elapsed, value, value, 1, segment) }
            }
            for bin in buckets.keys.sorted() {
                let bucket = buckets[bin]!
                result.append(ReviewPoint(id: result.count, elapsed: bucket.time/Double(bucket.count), mean: bucket.sum/Double(bucket.count),
                                          minimum: bucket.min, maximum: bucket.max, signal: "\(signal.name) · \(signal.pid) · \(signal.ecu)", segment: signal.id+"/\(bucket.segment)"))
            }
        }
        points = result
    }

    private func format(_ seconds: Double) -> String {
        let seconds = max(0, Int(seconds))
        return String(format: "%02d:%02d", seconds/60, seconds%60)
    }
}
