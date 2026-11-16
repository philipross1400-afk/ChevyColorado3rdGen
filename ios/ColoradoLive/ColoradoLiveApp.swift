import SwiftUI
import ExternalAccessory
import ColoradoCore

@main
struct ColoradoLiveApp: App {
    @StateObject private var model = LiveModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            DashboardView(model: model)
                .preferredColorScheme(.dark)
                .onChange(of: scenePhase) { phase in
                    if phase != .active { model.stop() }
                }
        }
    }
}

struct DashboardView: View {
    @ObservedObject var model: LiveModel
    private let accent = Color(red: 0.33, green: 0.85, blue: 0.82)
    private let columns = [GridItem(.flexible()), GridItem(.flexible())]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("COLORADO / LIVE").font(.title.bold())
                        Text("Read-only ECM monitoring").foregroundStyle(accent)
                        Text(model.status).font(.subheadline).foregroundStyle(.secondary)
                    }
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        LazyVGrid(columns: columns, spacing: 12) {
                            gauge("SPEED", value: value(0x0D, at: context.date), unit: "mph", range: 0...120)
                            gauge("ENGINE", value: value(0x0C, at: context.date), unit: "RPM", range: 0...6000)
                            gauge("BOOST / VACUUM", value: model.running ? OBD.boost(model.readings, now: context.date) : nil,
                                  unit: "psi gauge", range: -15...30)
                            gauge("COOLANT", value: value(0x05, at: context.date), unit: "°F", range: 100...260)
                        }
                    }
                    connection
                    VStack(alignment: .leading, spacing: 12) {
                        Text("SIGNALS").font(.headline)
                        TimelineView(.periodic(from: .now, by: 1)) { context in
                            ForEach(Sensor.all) { sensor in
                                let reading = model.readings[sensor.id]
                                let displayed = value(sensor.id, at: context.date)
                                HStack(alignment: .top) {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(sensor.name).font(.subheadline)
                                        Text(reading != nil && displayed == nil && reading?.usable == true ? "Stale · waiting for a new sample" :
                                                model.signalStatus[sensor.id] ?? "Not checked")
                                            .font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Text(displayed.map { String(format: "%.1f", $0) } ?? "—").monospacedDigit()
                                    Text(sensor.unit).font(.caption).foregroundStyle(.secondary)
                                }
                                Divider()
                            }
                        }
                    }
                    Text("Oil pressure, knock retard, transmission temperature, gear and slip are unavailable: definitions are unverified. Readings are unvalidated. Boost uses asynchronous pressure samples.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                .padding(20)
            }
            .background(Color(red: 0.063, green: 0.09, blue: 0.125))
            .tint(accent)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Text("3RD GEN").font(.caption.bold()).foregroundStyle(.secondary) }
            .alert("Connection or recording issue", isPresented: Binding(
                get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } }
            )) { Button("OK") { model.errorMessage = nil } } message: { Text(model.errorMessage ?? "") }
        }
    }

    private var connection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("OBDLINK MX+").font(.headline)
            if AccessoryTransport.configuredProtocols.isEmpty {
                Label("Accessory protocol configuration required", systemImage: "wrench.and.screwdriver")
                    .foregroundStyle(.orange)
                Text("This build needs the protocol identifier provided by OBDLink. See ios/README.md for the integration steps.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Toggle("Performance polling", isOn: $model.performance).disabled(model.running)
            Toggle("Parked setup confirmed", isOn: $model.parked).disabled(model.running)
            Text("Apply the parking brake, close other OBD apps, and idle outdoors. Set up before moving; passenger operation only. Switching away from this app stops polling and saves the recording.")
                .font(.footnote).foregroundStyle(.secondary)
            if model.running {
                Button("Stop & save", systemImage: "stop.circle", action: model.stop)
                    .buttonStyle(.borderedProminent)
            } else {
                ForEach(model.accessories, id: \.connectionID) { accessory in
                    Button("Connect & record · \(accessory.name)") { model.start(accessory) }
                        .buttonStyle(.borderedProminent)
                        .disabled(!model.parked || AccessoryTransport.configuredProtocols.isEmpty)
                }
                if model.accessories.isEmpty {
                    Text("Pair your MX+ in iPhone Settings → Bluetooth, then refresh. No compatible accessory is currently visible to this app.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Button("Refresh accessories", systemImage: "arrow.clockwise", action: model.refreshAccessories)
                if let url = model.csvURL {
                    ShareLink(item: url) { Label("Share recording CSV", systemImage: "square.and.arrow.up") }
                }
            }
        }
        .padding(16)
        .background(.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 18))
    }

    private func value(_ pid: Int, at date: Date) -> Double? {
        guard model.running, let reading = model.readings[pid], reading.usable,
              let sensor = Sensor.all.first(where: { $0.id == pid }) else { return nil }
        let age = date.timeIntervalSince(reading.time)
        return age >= 0 && age < max(3, sensor.interval*3) ? reading.value : nil
    }

    private func gauge(_ title: String, value: Double?, unit: String, range: ClosedRange<Double>) -> some View {
        VStack(spacing: 12) {
            Text(title).font(.caption.bold()).foregroundStyle(.secondary)
            Gauge(value: min(range.upperBound, max(range.lowerBound, value ?? range.lowerBound)), in: range) {
                Text(unit)
            } currentValueLabel: {
                Text(value.map { String(format: unit == "RPM" || unit == "mph" ? "%.0f" : "%.1f", $0) } ?? "—")
                    .monospacedDigit()
            }
            .gaugeStyle(.accessoryCircular).tint(accent).scaleEffect(1.25).padding(10)
            Text(value == nil ? "Waiting / unavailable" : "Unvalidated")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 18)
        .background(Color(red: 0.094, green: 0.137, blue: 0.192), in: RoundedRectangle(cornerRadius: 18))
    }
}
