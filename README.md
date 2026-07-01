# AI Reply Copilot

Mac desktop copilot that reads the current iMessage/Slack conversation and
drafts replies in your voice. See [prd_en.md](prd_en.md) / [prd_zh.md](prd_zh.md)
for the full product spec.

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

Reading is strictly read-only. Sending always requires confirmation (or an
explicit `--yes`) and only sends plain text — no private-API tricks.

## Requirements

- Python 3.9+ (no third-party runtime dependencies)
- macOS with **Full Disk Access** granted to the terminal/app running this
  (needed to read `~/Library/Messages/chat.db`).
- For Slack: `SLACK_BOT_TOKEN` with read scopes (`channels:history`,
  `groups:history`, `im:history`, `channels:read`, `users:read`).
- For `suggest`: an API key via `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.

## Install

```bash
cd side_projects/ai_reply_copilot
pip install -e ".[dev]"
```

## Usage

```bash
# List recently active conversations (shows chat IDs)
reply-copilot list
reply-copilot list --limit 10

# Show recent context for a conversation by its chat ID
reply-copilot show 42
reply-copilot show 42 --limit 30

# Point at a test database instead of the real chat.db
reply-copilot list --db /path/to/test/chat.db

# Generate 3 reply candidates for an iMessage conversation
export OPENAI_API_KEY=sk-...
reply-copilot suggest 42
reply-copilot suggest 42 --intent decline --tone professional
reply-copilot suggest 42 --draft "I can't make the meeting" --provider anthropic

# Slack (read-only)
export SLACK_BOT_TOKEN=xoxb-...
reply-copilot slack-list
reply-copilot slack-show C0123456789
reply-copilot suggest C0123456789 --source slack --tone friendly

# Send a reviewed reply (asks to confirm; --dry-run previews)
reply-copilot send --to "+15551234567" --text "Sounds good, see you at 7!"
reply-copilot send --source slack --to C0123456789 --text "On it" --dry-run

# End-to-end: read a conversation, get candidates, pick/edit, then send
reply-copilot reply 42 --tone friendly
reply-copilot reply C0123456789 --source slack --intent follow_up --dry-run
```

In `reply`, pick a candidate by number, type `e<n>` to edit one before
sending, or `q` to cancel. You confirm before anything is sent.

```bash
# Personal style profile (local-first; applied automatically to suggestions)
reply-copilot profile set --formality casual --no-emoji --language 中英双语
reply-copilot profile show
reply-copilot suggest 42 --ignore-profile   # opt out for one run

# Feedback on a reply (stored locally as JSONL)
reply-copilot feedback useful --text "Sounds good!" --source imessage --target 42
# ratings: useful | too_ai | wrong_tone | not_safe | not_like_me
```

Local state lives under `~/.ai_reply_copilot/` (override with
`AI_REPLY_COPILOT_HOME`). Nothing is uploaded; you can inspect or delete it.

You can also run it without installing:

```bash
python -m ai_reply_copilot.cli list
```

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
6. Menu-bar / desktop shell (Swift or Tauri) around this core.

See the 6-week roadmap in the PRD (section 21).
