# AI Reply Copilot

Mac desktop copilot that reads the current iMessage/Slack conversation and
drafts replies in your voice. See [prd_en.md](prd_en.md) / [prd_zh.md](prd_zh.md)
for the full product spec.

**Status:** Week 1–2 prototype — read-only iMessage reader + reply generation.

## What works now

- A read-only CLI that reads the local macOS `chat.db`, lists recently active
  conversations, and reconstructs conversation context (decoding both plain
  `text` and modern `attributedBody` message bodies).
- Reply generation: turn a conversation into a one-line understanding plus 3
  distinct reply candidates, with optional intent/tone/draft.

Reading is strictly read-only: it never writes to `chat.db` and never sends
anything. Generation drafts candidates for you to review, edit, and send
yourself.

## Requirements

- Python 3.9+ (no third-party runtime dependencies)
- macOS with **Full Disk Access** granted to the terminal/app running this
  (needed to read `~/Library/Messages/chat.db`).
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

# Generate 3 reply candidates for a conversation
export OPENAI_API_KEY=sk-...
reply-copilot suggest 42
reply-copilot suggest 42 --intent decline --tone professional
reply-copilot suggest 42 --draft "I can't make the meeting" --provider anthropic
```

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
2. Slack context reader (Socket Mode).
3. Sending path (Messages automation / Slack API) with review-before-send.

See the 6-week roadmap in the PRD (section 21).
