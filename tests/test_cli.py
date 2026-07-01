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


def test_cli_suggest_no_target_uses_most_recent(chat_db, capsys, monkeypatch):
    fake = FakeClient(
        json.dumps({"understanding": "Boss asks for review.", "candidates": ["On it", "Will do", "Sure"]})
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    code = cli.main(["suggest", "--db", str(chat_db)])
    out = capsys.readouterr().out
    assert code == 0
    # Chat 20 (boss@work.com) has the most recent message in the fixture.
    assert "Using most recent conversation: boss@work.com" in out
    _, user = fake.calls[0]
    assert "Can you review the deck before EOD?" in user


def test_cli_reply_no_target_uses_most_recent(chat_db, capsys, monkeypatch):
    fake = FakeClient(
        json.dumps({"understanding": "x", "candidates": ["A", "B", "C"]})
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    code = cli.main(["reply", "--db", str(chat_db), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Using most recent conversation: boss@work.com" in out
    assert "not sent" in out


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


def test_cli_send_imessage_dry_run(capsys):
    code = cli.main(["send", "--to", "+1555", "--text", "hello", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "About to send via imessage" in out
    assert "not sent" in out


def test_cli_send_slack_yes(capsys, monkeypatch):
    fake = _fake_slack()
    fake._responses["chat.postMessage"] = {"ok": True, "ts": "99.9"}
    monkeypatch.setattr(cli, "SlackClient", lambda: fake)
    code = cli.main(["send", "--source", "slack", "--to", "C1", "--text", "hi", "--yes"])
    out = capsys.readouterr().out
    assert code == 0
    assert fake.posted == [("chat.postMessage", {"channel": "C1", "text": "hi"})]
    assert "99.9" in out


def test_cli_send_cancelled(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = cli.main(["send", "--to", "+1555", "--text", "hello"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Cancelled." in out


def test_cli_profile_set_and_show(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    code = cli.main(["profile", "set", "--formality", "casual", "--no-emoji", "--language", "中文"])
    assert code == 0
    capsys.readouterr()
    code = cli.main(["profile", "show"])
    out = capsys.readouterr().out
    assert code == 0
    assert "casual" in out
    assert "no emoji" in out
    assert "中文" in out


def test_cli_feedback(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    code = cli.main(["feedback", "useful", "--text", "Sounds good!", "--source", "imessage"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Recorded 'useful'" in out
    from ai_reply_copilot import storage

    entries = storage.load_feedback()
    assert entries[0]["rating"] == "useful"


def test_cli_suggest_applies_saved_profile(chat_db, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    cli.main(["profile", "set", "--language", "中文"])
    capsys.readouterr()
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["好的"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    cli.main(["suggest", "10", "--db", str(chat_db)])
    _, user = fake.calls[0]
    assert "中文" in user


def test_cli_reply_imessage_dry_run(chat_db, capsys, monkeypatch):
    fake_llm = FakeClient(
        json.dumps(
            {"understanding": "Alex asks about tonight.", "candidates": ["Yes!", "No", "Maybe"]}
        )
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake_llm)
    # Pick candidate 1; dry-run needs no confirmation.
    monkeypatch.setattr("builtins.input", lambda _: "1")
    code = cli.main(["reply", "10", "--db", str(chat_db), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "1. Yes!" in out
    assert "not sent" in out
