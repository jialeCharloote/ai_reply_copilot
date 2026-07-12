// Charla — macOS menu-bar shell (scaffold).
//
// A menu-bar-only (accessory) app that drives the same Python engine as the CLI
// and the Slack app: it shells out to `charla suggest --json`, parses the
// drafts, and lists them in the menu. Selecting a draft copies it to the
// clipboard (the safe first action — no accidental send). Direct "send as me"
// and a global hotkey are the next steps; see macos/README.md.

import AppKit
import Foundation

/// Shape of `charla suggest --json`.
struct Suggestion: Decodable {
    let understanding: String
    let candidates: [String]
    let sensitive: [String]
    /// What the user still owes the other side: unanswered questions, decisions
    /// being waited on, deadlines. Written in the reply language.
    let openPoints: [String]
    /// The reply language Charla resolved for this conversation.
    let language: String?

    enum CodingKeys: String, CodingKey {
        case understanding, candidates, sensitive, language
        case openPoints = "open_points"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        understanding = try c.decodeIfPresent(String.self, forKey: .understanding) ?? ""
        candidates = try c.decodeIfPresent([String].self, forKey: .candidates) ?? []
        sensitive = try c.decodeIfPresent([String].self, forKey: .sensitive) ?? []
        openPoints = try c.decodeIfPresent([String].self, forKey: .openPoints) ?? []
        language = try c.decodeIfPresent(String.self, forKey: .language)
    }
}

enum DraftError: Error {
    case commandFailed(String)
    /// The CLI refused to send sensitive context to the cloud (exit 3). The user
    /// must confirm; we surface a "Draft anyway" item rather than a dead error.
    case needsSensitiveConsent(categories: String)
}

/// `cli.EXIT_SENSITIVE` — the gate refused, awaiting explicit consent.
private let exitSensitive: Int32 = 3

/// Run the `charla` CLI (found on PATH via /usr/bin/env) and decode its JSON.
func fetchSuggestion(extraArgs: [String]) -> Result<Suggestion, DraftError> {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = ["charla", "suggest", "--json"] + extraArgs

    let stdout = Pipe()
    let stderr = Pipe()
    process.standardOutput = stdout
    process.standardError = stderr

    do {
        try process.run()
    } catch {
        return .failure(.commandFailed("Couldn't launch `charla`: \(error.localizedDescription)"))
    }

    // Drain both pipes concurrently, then wait. Reading one to EOF before the
    // other deadlocks if the child fills the ~64KB buffer of the pipe we are not
    // reading — and stdout/stderr are both chatty (chatter is on stderr).
    var outData = Data()
    var errData = Data()
    let group = DispatchGroup()
    let queue = DispatchQueue.global(qos: .userInitiated)
    queue.async(group: group) { outData = stdout.fileHandleForReading.readDataToEndOfFile() }
    queue.async(group: group) { errData = stderr.fileHandleForReading.readDataToEndOfFile() }
    group.wait()
    process.waitUntilExit()

    let err = String(data: errData, encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

    if process.terminationStatus == exitSensitive {
        return .failure(.needsSensitiveConsent(categories: err))
    }
    if process.terminationStatus != 0 {
        return .failure(.commandFailed(err.isEmpty ? "charla exited \(process.terminationStatus)" : err))
    }
    do {
        return .success(try JSONDecoder().decode(Suggestion.self, from: outData))
    } catch {
        // stdout is the machine-readable channel; anything unparseable there is a
        // bug worth showing rather than swallowing behind a generic message.
        let raw = String(data: outData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return .failure(.commandFailed("Couldn't parse drafts: \(error.localizedDescription)\n\(raw.prefix(200))"))
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "Charla"
        showIdleMenu()
    }

    // MARK: menu states

    private func showIdleMenu() {
        let menu = NSMenu()
        menu.addItem(makeItem("Draft reply to most recent…", #selector(draftMostRecent)))
        menu.addItem(.separator())
        menu.addItem(makeItem("Quit Charla", #selector(quit)))
        statusItem.menu = menu
    }

    private func showLoadingMenu() {
        let menu = NSMenu()
        let loading = NSMenuItem(title: "Drafting…", action: nil, keyEquivalent: "")
        loading.isEnabled = false
        menu.addItem(loading)
        statusItem.menu = menu
    }

    private func showDrafts(_ suggestion: Suggestion) {
        let menu = NSMenu()
        if !suggestion.understanding.isEmpty {
            let header = NSMenuItem(title: suggestion.understanding, action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
        }
        if !suggestion.sensitive.isEmpty {
            let note = NSMenuItem(
                title: "⚠︎ contains \(suggestion.sensitive.joined(separator: ", "))",
                action: nil, keyEquivalent: "")
            note.isEnabled = false
            menu.addItem(note)
        }
        // What you still owe them — the reason to open this menu at all.
        if !suggestion.openPoints.isEmpty {
            menu.addItem(.separator())
            let title = NSMenuItem(title: "你还没回应 / Waiting on you:", action: nil, keyEquivalent: "")
            title.isEnabled = false
            menu.addItem(title)
            for point in suggestion.openPoints {
                let item = NSMenuItem(title: "   • \(point)", action: nil, keyEquivalent: "")
                item.isEnabled = false
                menu.addItem(item)
            }
        }
        if let language = suggestion.language {
            let item = NSMenuItem(title: "Reply language: \(language)", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        }
        menu.addItem(.separator())
        for (index, candidate) in suggestion.candidates.enumerated() {
            let item = makeItem("\(index + 1). \(candidate)", #selector(copyDraft(_:)))
            item.representedObject = candidate  // full text; title may be truncated by the OS
            menu.addItem(item)
        }
        menu.addItem(.separator())
        menu.addItem(makeItem("Draft again", #selector(draftMostRecent)))
        menu.addItem(makeItem("Quit Charla", #selector(quit)))
        statusItem.menu = menu
        statusItem.button?.performClick(nil)  // reopen so the drafts are visible immediately
    }

    /// Shown when the CLI refuses to upload sensitive context (exit 3). Nothing
    /// has left the machine yet; continuing is an explicit, per-run choice.
    private func showSensitiveGate(_ detail: String) {
        let menu = NSMenu()
        for line in detail.split(separator: "\n").prefix(3) {
            let item = NSMenuItem(title: String(line), action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        }
        menu.addItem(.separator())
        menu.addItem(makeItem("Draft anyway (sends context to the cloud)", #selector(draftAnyway)))
        menu.addItem(makeItem("Cancel", #selector(showIdleMenuAction)))
        menu.addItem(makeItem("Quit Charla", #selector(quit)))
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
    }

    @objc private func showIdleMenuAction() { showIdleMenu() }

    private func showError(_ message: String) {
        let menu = NSMenu()
        let item = NSMenuItem(title: message, action: nil, keyEquivalent: "")
        item.isEnabled = false
        menu.addItem(item)
        menu.addItem(.separator())
        menu.addItem(makeItem("Try again", #selector(draftMostRecent)))
        menu.addItem(makeItem("Quit Charla", #selector(quit)))
        statusItem.menu = menu
    }

    // MARK: actions

    @objc private func draftMostRecent() {
        draft(allowSensitive: false)
    }

    /// The user saw what the scanner found and chose to continue anyway. This is
    /// the app's half of the sensitive-content gate: the CLI refuses by default,
    /// and only an explicit click here passes --allow-sensitive.
    @objc private func draftAnyway() {
        draft(allowSensitive: true)
    }

    private func draft(allowSensitive: Bool) {
        showLoadingMenu()
        DispatchQueue.global(qos: .userInitiated).async {
            let result = fetchSuggestion(extraArgs: allowSensitive ? ["--allow-sensitive"] : [])
            DispatchQueue.main.async {
                switch result {
                case .success(let suggestion): self.showDrafts(suggestion)
                case .failure(.commandFailed(let message)): self.showError(message)
                case .failure(.needsSensitiveConsent(let categories)):
                    self.showSensitiveGate(categories)
                }
            }
        }
    }

    @objc private func copyDraft(_ sender: NSMenuItem) {
        guard let text = sender.representedObject as? String else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    @objc private func quit() {
        NSApplication.shared.terminate(nil)
    }

    // MARK: helpers

    private func makeItem(_ title: String, _ action: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        return item
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // menu-bar only, no Dock icon
let delegate = AppDelegate()
app.delegate = delegate
app.run()
