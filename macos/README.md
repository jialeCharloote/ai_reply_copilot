# Charla — macOS menu-bar shell (scaffold)

A menu-bar-only app that drives the same Python engine as the CLI and Slack app.
It shells out to `charla suggest --json`, shows the 3 drafts in the menu, and
copies the one you pick to the clipboard. This is a **starting scaffold** — the
Python engine (`ai_reply_copilot/drafting.py`) does the real work.

## Build & run

Needs the `charla` CLI installed and on your PATH, plus `ANTHROPIC_API_KEY` in
the environment the app inherits:

```bash
pip install -e ".."          # installs the `charla` CLI (from the repo root)
export ANTHROPIC_API_KEY=sk-ant-...
cd macos
swift run                    # launches the menu-bar app (look for "Charla" in the menu bar)
```

Click **Charla → Draft reply to most recent…** → pick a draft to copy it.

## What works / what's next

- **Works:** menu-bar (accessory) app; runs the engine; parses `suggest --json`
  (understanding + 3 candidates + sensitive-content flags); copy-to-clipboard.
- **Next (in rough order):**
  1. **Full Disk Access** — reading `~/Library/Messages/chat.db` requires it.
     Grant it to the built app (or to your terminal when running `swift run`) in
     System Settings → Privacy & Security → Full Disk Access.
  2. **Global hotkey** (e.g. ⌥⌘R) to open the drafts without using the mouse —
     needs a small Carbon `RegisterEventHotKey` or `CGEventTap` shim.
  3. **Send as you** — call `charla reply`/`send` (which already routes 1:1 vs
     group iMessage correctly) instead of copy-only, with a confirm step.
  4. **Active-conversation detection** — today it drafts for the most recent
     thread; use the Accessibility API on the frontmost Messages/Slack window to
     target the conversation actually on screen.
  5. **Packaging** — wrap as a signed `.app` bundle (`LSUIElement`) for install
     outside `swift run`.

The engine boundary is `charla suggest --json` / `charla reply`, so none of the
above touches the drafting logic — the same seam the Slack app uses.
