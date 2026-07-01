"""End-to-end CLI tests."""

from __future__ import annotations

import json

from ai_reply_copilot import cli
from ai_reply_copilot.llm import FakeClient


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
