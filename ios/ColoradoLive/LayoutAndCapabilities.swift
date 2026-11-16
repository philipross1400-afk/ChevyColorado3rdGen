import SwiftUI
import ColoradoCore

struct LayoutEditor: View {
    @Binding var layout: DashboardLayout
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                Section("Presets") {
                    ForEach(DashboardLayout.presets, id: \.0) { name, signals in
                        Button(name) { layout = DashboardLayout(signals: signals, largeCards: layout.large_cards) }
                    }
                }
                Section("Gauges · drag to reorder") {
                    ForEach(layout.signals, id: \.self) { key in Text(GaugeSignal.find(key)?.name ?? key) }
                        .onMove { indices, destination in layout.signals.move(fromOffsets: indices, toOffset: destination) }
                        .onDelete { indices in
                            let remaining = layout.signals.enumerated().filter { !indices.contains($0.offset) }.map(\.element)
                            if !remaining.isEmpty { layout.signals = remaining }
                        }
                    if layout.signals.count < 4 {
                        Menu("Add gauge") {
                            ForEach(GaugeSignal.all.filter { !layout.signals.contains($0.id) }) { signal in
                                Button(signal.name) { layout.signals.append(signal.id) }
                            }
                        }
                    }
                }
                Section {
                    Toggle("Larger gauge cards", isOn: $layout.large_cards)
                    Text("Layouts are saved on this device. Choosing a gauge does not change polling or enable an unsupported sensor.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .environment(\.editMode, .constant(.active))
            .navigationTitle("Dashboard layout")
            .toolbar { Button("Done") { dismiss() } }
        }
    }
}

struct CapabilityView: View {
    let snapshot: CapabilitySnapshot?
    var quality: [Int: String] = [:]
    var body: some View {
        List {
            Section("ECM discovery") {
                if let snapshot {
                    Text(snapshot.name)
                    Text("\(snapshot.profile) · ECM 18DAF111")
                    Text("Advertised PIDs: " + snapshot.supported.map { String(format: "%02X", $0) }.joined(separator: " "))
                        .font(.caption).foregroundStyle(.secondary)
                } else { Text("No capability snapshot available. A live connection saves discovery results with its recording.") }
            }
            Section("Implemented signals") {
                ForEach(Sensor.all) { sensor in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(sensor.name)
                        Text(snapshot?.status(pid: sensor.id) ?? "Not checked").font(.caption).foregroundStyle(.secondary)
                        if let status = quality[sensor.id] { Text(status).font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
            Section("Definitions still unverified") {
                ForEach(CapabilitySnapshot.unverified, id: \.self) { Text($0).foregroundStyle(.secondary) }
            }
            Text("Advertised parent PID support does not guarantee that every extended channel exists. Response flags and sample quality determine which values are shown.")
                .font(.footnote).foregroundStyle(.secondary)
        }
        .navigationTitle("Vehicle capabilities")
    }
}
