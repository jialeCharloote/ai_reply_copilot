"""End-to-end CLI tests."""

from __future__ import annotations

import json

from ai_reply_copilot import cli
from ai_reply_copilot.llm import FakeClient
from ai_reply_copilot.slack import FakeSlackClient


def _fake_slack():
    return FakeSlackClient(
        {
            "conversations.list": {
                "ok": True,
                "channels": [{"id": "C1", "name": "general", "is_im": False}],
            },
            "conversations.history": lambda p: {
                "ok": True,
                "messages": [
                    {"ts": "1000.2", "text": "Yes", "user": "U_ME"},
                    {"ts": "1000.1", "text": "Deploy tonight?", "user": "U2"},
                ],
            },
            "users.info": lambda p: {
                "ok": True,
                "user": {"profile": {"display_name": "Alex"}},
            },
        }
    )


def test_cli_list(chat_db, capsys):
    code = cli.main(["list", "--db", str(chat_db)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Alex" in out
    assert "boss@work.com" in out


def test_cli_show(chat_db, capsys):
    code = cli.main(["show", "10", "--db", str(chat_db)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Hey are you free tonight?" in out
    assert "Me: Maybe" in out


def test_cli_show_unknown_chat(chat_db, capsys):
    code = cli.main(["show", "999", "--db", str(chat_db)])
    out = capsys.readouterr().out
    assert code == 0
    assert "No messages found" in out


def test_cli_missing_db(tmp_path, capsys):
    code = cli.main(["list", "--db", str(tmp_path / "nope.db")])
    err = capsys.readouterr().err
    assert code == 2
    assert "error:" in err


def test_cli_suggest(chat_db, capsys, monkeypatch):
    fake = FakeClient(
        json.dumps(
            {
                "understanding": "Alex is asking about tonight's plans.",
                "candidates": ["Sounds good!", "Can't tonight, tomorrow?", "7 works!"],
            }
        )
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)

    code = cli.main(["suggest", "10", "--db", str(chat_db), "--tone", "friendly"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Understanding: Alex is asking" in out
    assert "1. Sounds good!" in out
    assert "3. 7 works!" in out


def test_cli_slack_list(capsys, monkeypatch):
    fake = _fake_slack()
    monkeypatch.setattr(cli, "SlackClient", lambda: fake)
    code = cli.main(["slack-list"])
    out = capsys.readouterr().out
    assert code == 0
    assert "#general" in out


def test_cli_slack_show(capsys, monkeypatch):
    fake = _fake_slack()
    monkeypatch.setattr(cli, "SlackClient", lambda: fake)
    code = cli.main(["slack-show", "C1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Deploy tonight?" in out
    assert "Me: Yes" in out


def test_cli_suggest_slack_source(capsys, monkeypatch):
    fake_slack = _fake_slack()
    monkeypatch.setattr(cli, "SlackClient", lambda: fake_slack)
    fake_llm = FakeClient(
        json.dumps({"understanding": "Deploy question.", "candidates": ["Ship it", "Wait for review", "Tomorrow"]})
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake_llm)

    code = cli.main(["suggest", "C1", "--source", "slack"])
    out = capsys.readouterr().out
    assert code == 0
    assert "1. Ship it" in out
    # The Slack context should have reached the LLM.
    _, user = fake_llm.calls[0]
    assert "Deploy tonight?" in user
