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
}

enum DraftError: Error { case commandFailed(String) }

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
    process.waitUntilExit()

    let outData = stdout.fileHandleForReading.readDataToEndOfFile()
    if process.terminationStatus != 0 {
        let err = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        return .failure(.commandFailed(err.isEmpty ? "charla exited \(process.terminationStatus)" : err))
    }
    do {
        return .success(try JSONDecoder().decode(Suggestion.self, from: outData))
    } catch {
        return .failure(.commandFailed("Couldn't parse drafts: \(error.localizedDescription)"))
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
        showLoadingMenu()
        DispatchQueue.global(qos: .userInitiated).async {
            let result = fetchSuggestion(extraArgs: [])
            DispatchQueue.main.async {
                switch result {
                case .success(let suggestion): self.showDrafts(suggestion)
                case .failure(.commandFailed(let message)): self.showError(message)
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
