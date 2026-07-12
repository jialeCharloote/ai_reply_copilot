# Charla

Mac desktop copilot that reads the current iMessage/Slack conversation and
drafts replies in your voice. The name comes from Charlotte (the founder) and
means "a chat" in Spanish. See [prd_en.md](prd_en.md) / [prd_zh.md](prd_zh.md)
for the full product spec, and [positioning.md](positioning.md) for the
users & go-to-market positioning.

**Status:** Working CLI core loop — iMessage + Slack readers, reply generation,
a reviewed sending path, and an end-to-end `reply` command.

## What works now

- A read-only CLI that reads the local macOS `chat.db`, lists recently active
  conversations, and reconstructs conversation context (decoding both plain
  `text` and modern `attributedBody` message bodies).
- A read-only Slack reader (Web API) that lists channels/DMs and reconstructs
  channel context into the same message model.
- Reply generation: turn a conversation (iMessage or Slack) into a one-line
  understanding plus 3 distinct reply candidates, with optional intent/tone/draft.
- A sending path: send a reviewed reply via iMessage (Messages automation) or
  Slack (`chat.postMessage`), with a confirmation prompt and `--dry-run`.
- An end-to-end `reply` command that reads a conversation, generates candidates,
  lets you pick or edit one, and sends after confirmation — the PRD core loop.
- Local-first personal style profile (applied to suggestions) and a feedback log.
- **Bilingual, per conversation.** The reply language belongs to the *thread*,
  not to a global setting — your work Slack is English and your friends are
  Chinese, so one global switch is always wrong for half your day. Charla detects
  the language from the recent messages (locally, no model call), and Chinese
  keeps English loanwords ("这个 API 什么时候 deploy?" is Chinese, not "mixed").
  You can pin a conversation with `--language X --remember-language`, or override
  one run with `--language`. The style profile's language is only a **fallback**
  for when a thread gives no signal — it never overrides the conversation.
- **You can see what it read.** Before the drafts, Charla prints the exact
  messages it used, marks what arrived *since your last reply*, says which
  language it will answer in and why, and lists what is still waiting on you
  (unanswered questions, decisions, deadlines). `--no-context` hides the panel.
- **Learns your voice from messages you actually sent.** `charla voice learn`
  reads your own past messages out of `chat.db` (`is_from_me`) and works out how
  you really write — length, emoji, punctuation, capitalisation, how much you
  code-switch — then keeps a handful verbatim as examples. A model imitates real
  sentences far better than it imitates the adjective "casual". Statistics never
  leave the machine; the verbatim examples *are* part of the prompt, so they are
  uploaded with every draft, which is why saving them requires an explicit yes,
  why messages containing secrets are screened out first, and why
  `--examples 0` gives you statistics only. `charla voice forget` undoes it, and
  `CHARLA_NO_VOICE=1` turns it off for the Slack app without discarding what was
  learned.

  Screening looks at the message **and what it was replying to** — the answer to
  "what's the wifi password?" is a bare word like `sunshinecoast`, and nothing in
  that word marks it as a secret; only the question does. Exemplars are also
  re-screened every time they are loaded, so tightening the rules retroactively
  protects a profile that was saved under looser ones.
- A **sensitive-content gate, split by severity.** An actual secret — a password,
  an API key, a card/SSN number, an explicit "don't share this" — stops and asks.
  A sensitive *topic* — a salary, an offer letter, a contract, a diagnosis — is
  reported but never interrupts. That split is deliberate: the first wedge is
  professional replies, so a gate that fired on "salary" would fire on nearly
  every conversation Charla exists for, and users would learn to click past it.
  The gate runs before *any* cloud call, scans your `--draft` too, and is not
  waived by `--dry-run` (which only suppresses the *send* — the context is still
  uploaded to draft) nor by `--yes` (a send flag, not a privacy decision). When
  there is nobody to ask (`--json`), it refuses instead of uploading and exits
  **3**; pass `--allow-sensitive` to opt in. The menu-bar app keys off that exit
  code to offer its own "Draft anyway", so the gate is a real choice on every
  surface rather than a dead end.

Reading is strictly read-only. Sending always requires confirmation (or an
explicit `--yes`) and only sends plain text — no private-API tricks.

## Requirements

- Python 3.9+ (no third-party runtime dependencies)
- macOS with **Full Disk Access** granted to the terminal/app running this
  (needed to read `~/Library/Messages/chat.db`).
- For Slack reads: a token with read scopes (`channels:history`, `groups:history`,
  `im:history`, `channels:read`, `users:read`) — `SLACK_USER_TOKEN` is used when
  set, otherwise `SLACK_BOT_TOKEN`.
- For Slack **sends**: `SLACK_USER_TOKEN` (`chat:write`). Charla posts as you, so
  it will not fall back to the bot token.
- For `suggest`: an API key. The default provider is Anthropic
  (`ANTHROPIC_API_KEY`); `--provider openai` uses `OPENAI_API_KEY`.

## Install

```bash
cd side_projects/ai_reply_copilot
pip install -e ".[dev]"
```

## Usage

```bash
# List recently active conversations (shows chat IDs)
charla list
charla list --limit 10

# Show recent context for a conversation by its chat ID
charla show 42
charla show 42 --limit 30

# Point at a test database instead of the real chat.db
charla list --db /path/to/test/chat.db

# Generate 3 reply candidates for an iMessage conversation
export OPENAI_API_KEY=sk-...
charla suggest 42
charla suggest 42 --intent decline --tone professional
charla suggest 42 --draft "I can't make the meeting" --provider anthropic

# Slack (read-only)
export SLACK_BOT_TOKEN=xoxb-...
charla slack-list
charla slack-show C0123456789
charla suggest C0123456789 --source slack --tone friendly

# Send a reviewed reply (asks to confirm; --dry-run previews)
charla send --to "+15551234567" --text "Sounds good, see you at 7!"
charla send --source slack --to C0123456789 --text "On it" --dry-run

# End-to-end: read a conversation, get candidates, pick/edit, then send
charla reply 42 --tone friendly
charla reply C0123456789 --source slack --intent follow_up --dry-run

# No target? Charla uses your most recent conversation
charla reply --tone professional
charla suggest
```

In `reply`, pick a candidate by number, type `e<n>` to edit one before
sending, or `q` to cancel. You confirm before anything is sent.

```bash
# Language follows the conversation. Nothing to configure for the common case:
charla reply C0123456789 --source slack   # English channel -> English drafts
charla reply 42                           # 中文对话        -> 中文回复

# Pin one conversation (e.g. a Chinese colleague you always write to in English)
charla suggest 42 --language English --remember-language
charla suggest 42 --language 中文          # just this once

# Learn how you actually write, from your own past messages. Do this once.
charla voice learn                  # shows what it learned, asks before saving
charla voice learn --examples 0     # statistics only — nothing of yours uploaded
charla voice show                   # incl. exactly which messages get uploaded
charla voice forget

# Personal style profile (local-first; applied automatically to suggestions).
# --language here is only a FALLBACK for threads with no language signal; it
# does not override the conversation.
charla profile set --formality casual --no-emoji --language 中文
charla profile show
charla suggest 42 --ignore-profile   # opt out of profile AND voice for one run

# Feedback on a reply (stored locally as JSONL)
charla feedback useful --text "Sounds good!" --source imessage --target 42
# ratings: useful | too_ai | wrong_tone | not_safe | not_like_me
```

Local state lives under `~/.ai_reply_copilot/` (override with
`AI_REPLY_COPILOT_HOME`). Nothing is uploaded; you can inspect or delete it.

You can also run it without installing:

```bash
python -m ai_reply_copilot.cli list
```

## Slack app (draft & send as you)

The Slack front-end adds a **message-action shortcut** ("Draft reply (Charla)"):
pick a message → get 3 in-voice drafts in a modal → send the one you pick/edit
**as yourself** (not as a bot). It runs over Socket Mode, so no public URL is
needed — good for local dogfooding.

```bash
pip install -e ".[dev,slack]"
cp .env.example .env   # fill in the tokens, then export them (or use direnv)
python -m ai_reply_copilot.slack_app
```

Create a Slack app with **Socket Mode + Interactivity** enabled and a message
shortcut whose Callback ID is `charla_draft_reply`. Tokens needed (see
`.env.example`): app-level `SLACK_APP_TOKEN` (xapp, scope `connections:write`),
`SLACK_BOT_TOKEN` (xoxb, to open the modal), and **your** `SLACK_USER_TOKEN`
(xoxp, user scopes `channels:history`, `groups:history`, `im:history`,
`mpim:history`, `users:read`, `chat:write`) — the user token is what reads what
you can see and posts replies as you. If the thread trips the sensitive-content
scanner, Charla asks before sending context to the cloud model.

The generation core stays dependency-free; only the Slack app needs `slack_bolt`.
The shared engine lives in `drafting.py`, so later surfaces (menu-bar app,
browser extension) are thin front-ends over the same code.

## Development

```bash
python -m pytest
```

Tests build a temporary SQLite database with the `chat.db` schema, so they run
anywhere without Full Disk Access or a real Messages history.

## Roadmap (next)

1. ~~Prompt template + LLM call: turn rendered context into 3 reply candidates.~~ done
2. ~~Slack context reader.~~ done
3. ~~Sending path (Messages automation / Slack API) with review-before-send.~~ done
4. ~~End-to-end interactive flow: read → suggest → pick → send in one command.~~ done
5. ~~Personal style profile persistence and feedback loop.~~ done
6. ~~Slack app: draft & send as you (message-action shortcut, Socket Mode).~~ done
7. Menu-bar / desktop shell (Swift or Tauri) around the shared `drafting.py` engine.

See the 6-week roadmap in the PRD (section 21).
