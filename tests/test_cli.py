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
    captured = capsys.readouterr()
    assert code == 0
    # Chat 20 (boss@work.com) has the most recent message in the fixture.
    # Chatter goes to stderr so stdout stays parseable (see --json below).
    assert "Using most recent conversation: boss@work.com" in captured.err
    _, user = fake.calls[0]
    assert "Can you review the deck before EOD?" in user


def test_cli_reply_no_target_uses_most_recent(chat_db, capsys, monkeypatch):
    fake = FakeClient(
        json.dumps({"understanding": "x", "candidates": ["A", "B", "C"]})
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    code = cli.main(["reply", "--db", str(chat_db), "--dry-run"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Using most recent conversation: boss@work.com" in captured.err
    assert "not sent" in captured.out


def test_cli_suggest_gates_sensitive_context_before_the_cloud_call(chat_db, capsys, monkeypatch):
    # Regression: `suggest` used to print the warning and upload anyway.
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr(cli, "scan_blocking", lambda text: ["credentials"])
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = cli.main(["suggest", "10", "--db", str(chat_db)])
    err = capsys.readouterr().err
    assert code == cli.EXIT_SENSITIVE
    assert "credentials" in err
    assert fake.calls == []  # nothing left the machine


def test_cli_suggest_sensitive_proceeds_on_yes(chat_db, capsys, monkeypatch):
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr(cli, "scan_blocking", lambda text: ["credentials"])
    monkeypatch.setattr("builtins.input", lambda _: "y")
    code = cli.main(["suggest", "10", "--db", str(chat_db)])
    assert code == 0
    assert len(fake.calls) == 1


def test_cli_suggest_json_refuses_sensitive_rather_than_uploading(chat_db, capsys, monkeypatch):
    # --json has no human to ask, and prompting would corrupt stdout. It must
    # refuse, not upload and report "sensitive" after the fact.
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr(cli, "scan_blocking", lambda text: ["credentials"])
    code = cli.main(["suggest", "10", "--db", str(chat_db), "--json"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_SENSITIVE
    assert fake.calls == []
    assert captured.out.strip() == ""  # no partial JSON on stdout
    assert "--allow-sensitive" in captured.err


def test_cli_suggest_allow_sensitive_opts_in(chat_db, capsys, monkeypatch):
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr(cli, "scan_blocking", lambda text: ["credentials"])
    code = cli.main(["suggest", "10", "--db", str(chat_db), "--json", "--allow-sensitive"])
    assert code == 0
    assert len(fake.calls) == 1


def test_cli_scans_the_users_own_draft_too(chat_db, capsys, monkeypatch):
    # --draft is embedded in the prompt just like the conversation is, so it has
    # to be scanned; it used not to be.
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = cli.main(
        ["suggest", "10", "--db", str(chat_db), "--draft", "the password is hunter2"]
    )
    err = capsys.readouterr().err
    assert code == cli.EXIT_SENSITIVE
    assert "credentials" in err
    assert fake.calls == []


def test_cli_reply_sensitive_cancel(chat_db, capsys, monkeypatch):
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr(cli, "scan_blocking", lambda text: ["credentials"])
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = cli.main(["reply", "10", "--db", str(chat_db)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_SENSITIVE
    assert "credentials" in captured.err
    assert "Cancelled." in captured.out
    # The LLM must not have been called after cancelling.
    assert fake.calls == []


def test_cli_reply_dry_run_still_gates_sensitive_context(chat_db, capsys, monkeypatch):
    # Regression: --dry-run used to skip the gate — but it only suppresses the
    # *send*; the context is still uploaded to draft. Declining must stop that.
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["A", "B", "C"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr(cli, "scan_blocking", lambda text: ["credentials"])
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = cli.main(["reply", "10", "--db", str(chat_db), "--dry-run"])
    assert code == cli.EXIT_SENSITIVE
    assert fake.calls == []


def test_cli_reply_yes_does_not_waive_the_privacy_gate(chat_db, capsys, monkeypatch):
    # --yes is a *send* flag; it must not silently authorize a cloud upload.
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["A", "B", "C"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    monkeypatch.setattr(cli, "scan_blocking", lambda text: ["credentials"])
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = cli.main(["reply", "10", "--db", str(chat_db), "--dry-run", "--yes"])
    assert code == cli.EXIT_SENSITIVE
    assert fake.calls == []


def test_cli_slack_list(capsys, monkeypatch):
    fake = _fake_slack()
    monkeypatch.setattr(cli, "read_client", lambda: fake)
    code = cli.main(["slack-list"])
    out = capsys.readouterr().out
    assert code == 0
    assert "#general" in out


def test_cli_slack_show(capsys, monkeypatch):
    fake = _fake_slack()
    monkeypatch.setattr(cli, "read_client", lambda: fake)
    code = cli.main(["slack-show", "C1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Deploy tonight?" in out
    assert "Me: Yes" in out


def test_cli_suggest_slack_source(capsys, monkeypatch):
    fake_slack = _fake_slack()
    monkeypatch.setattr(cli, "read_client", lambda: fake_slack)
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
    monkeypatch.setattr(cli, "posting_client", lambda: fake)
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
    # The profile language is now shown as a *fallback*, not an override.
    assert "中文" in out
    assert "fallback language" in out


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
    # The profile language must NOT override an English conversation: fixture
    # chat 10 is in English, so the reply language stays English even though the
    # profile says 中文. (A global override would put Chinese into a work channel.)
    assert "Reply language: English" in user
    assert "Reply language: 中文" not in user


def test_cli_suggest_json(chat_db, capsys, monkeypatch):
    fake = FakeClient(
        json.dumps(
            {"understanding": "Alex asks about tonight.", "candidates": ["Yes!", "No", "7 works"]}
        )
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    code = cli.main(["suggest", "10", "--db", str(chat_db), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    payload = json.loads(out)  # exactly one JSON line, no human-readable noise
    assert payload["candidates"] == ["Yes!", "No", "7 works"]
    assert payload["understanding"].startswith("Alex")
    assert payload["source"] == "imessage"
    assert payload["target"] == "10"
    assert payload["sensitive"] == []


def test_cli_reply_group_sends_by_chat_guid(group_chat_db, capsys, monkeypatch):
    from ai_reply_copilot.send import SendResult

    fake_llm = FakeClient(
        json.dumps({"understanding": "launch", "candidates": ["On it", "Give me 10", "Posting now"]})
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake_llm)
    monkeypatch.setattr("builtins.input", lambda _: "1")

    captured = {}

    def fake_chat_send(guid, text, dry_run=False):
        captured["guid"] = guid
        captured["text"] = text
        return SendResult("imessage", guid, text, dry_run, "dry-run (not sent)")

    def fail_participant(*args, **kwargs):  # groups must not use the 1:1 path
        raise AssertionError("group reply must not use participant send")

    monkeypatch.setattr(cli, "send_imessage_to_chat", fake_chat_send)
    monkeypatch.setattr(cli, "send_imessage", fail_participant)

    code = cli.main(["reply", "30", "--db", str(group_chat_db), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert captured["guid"] == "iMessage;+;chat9999"
    assert captured["text"] == "On it"
    assert "not sent" in out


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


def test_cli_suggest_json_no_target_emits_only_json_on_stdout(chat_db, capsys, monkeypatch):
    # Regression: _resolve_target printed "Using most recent conversation: …" to
    # stdout ahead of the payload, so the macOS app — which decodes the whole
    # stdout buffer and passes no target — always failed with "Couldn't parse
    # drafts". This is that exact invocation.
    fake = FakeClient(
        json.dumps({"understanding": "Boss asks for review.", "candidates": ["On it", "Will do", "Sure"]})
    )
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    code = cli.main(["suggest", "--json", "--db", str(chat_db)])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)  # what JSONDecoder does in main.swift
    assert payload["candidates"] == ["On it", "Will do", "Sure"]
    assert payload["target"] == "20"
    # The chatter is still shown — just not on the machine-readable channel.
    assert "Using most recent conversation" in captured.err


def test_cli_send_slack_dry_run_needs_no_user_token(capsys, monkeypatch):
    # A dry run makes no network call, so it must not demand a posting token.
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)
    code = cli.main(["send", "--source", "slack", "--to", "C1", "--text", "hi", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "not sent" in out


def test_cli_reply_slack_validates_the_posting_token_before_calling_the_llm(chat_db, capsys, monkeypatch):
    # Deferring posting_client() into send_fn meant the missing-token error only
    # surfaced after the LLM was billed and the user had picked a draft.
    fake_llm = FakeClient(json.dumps({"understanding": "x", "candidates": ["a", "b", "c"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake_llm)
    monkeypatch.setattr(cli, "read_client", lambda: _fake_slack())
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)

    code = cli.main(["reply", "C1", "--source", "slack"])
    assert code == 2
    assert "SLACK_USER_TOKEN" in capsys.readouterr().err
    assert fake_llm.calls == []  # failed before spending a cloud call


def test_cli_json_sensitive_field_matches_what_the_gate_scanned(chat_db, capsys, monkeypatch):
    # The payload used to rescan the context only, dropping --draft, so it could
    # report "sensitive": [] for a run the gate had flagged on the draft.
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    code = cli.main(
        ["suggest", "10", "--db", str(chat_db), "--json", "--allow-sensitive",
         "--draft", "the password is hunter2"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["sensitive"] == ["credentials"]


# --- per-conversation language --------------------------------------------------


def test_cli_shows_the_context_it_read_and_what_is_unanswered(chat_db, capsys, monkeypatch):
    fake = FakeClient(json.dumps({
        "understanding": "Boss is waiting on you.",
        "open_points": ["Confirm Thursday", "Legal sign-off on slide 12"],
        "candidates": ["Thursday works", "Yes", "On it"],
    }, ensure_ascii=False))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    code = cli.main(["suggest", "10", "--db", str(chat_db)])
    out = capsys.readouterr().out
    assert code == 0
    # You can see exactly what Charla read...
    assert "Charla read" in out
    assert "Hey are you free tonight?" in out
    # ...what language it will answer in, and why...
    assert "Reply language:" in out
    # ...and what you still owe them.
    assert "Confirm Thursday" in out
    assert "Legal sign-off on slide 12" in out


def test_cli_no_context_suppresses_the_panel(chat_db, capsys, monkeypatch):
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["a"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    cli.main(["suggest", "10", "--db", str(chat_db), "--no-context"])
    assert "Charla read" not in capsys.readouterr().out


def test_cli_profile_language_does_not_leak_into_an_english_conversation(
    chat_db, capsys, tmp_path, monkeypatch
):
    # The whole point: your Slack is English, your friends are Chinese. Setting a
    # profile language must not turn the English work thread's replies Chinese.
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    cli.main(["profile", "set", "--language", "中文"])
    capsys.readouterr()

    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    cli.main(["suggest", "10", "--db", str(chat_db)])  # fixture chat 10 is English
    _system, user = fake.calls[0]
    assert "Reply language: English" in user
    assert "Reply language: 中文" not in user


def test_cli_remember_language_locks_the_conversation(chat_db, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["ok"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)

    # Chat 10 is English; pin it to 中文 and it stays pinned on the next run.
    cli.main(["suggest", "10", "--db", str(chat_db), "--language", "中文", "--remember-language"])
    assert "Reply language: 中文" in fake.calls[-1][1]

    cli.main(["suggest", "10", "--db", str(chat_db)])  # no flags this time
    assert "Reply language: 中文" in fake.calls[-1][1]

    # A different conversation is unaffected by that lock.
    cli.main(["suggest", "20", "--db", str(chat_db)])
    assert "Reply language: 中文" not in fake.calls[-1][1]


def test_cli_ignore_profile_also_ignores_the_profile_language(chat_db, capsys, tmp_path, monkeypatch):
    # --ignore-profile is documented as "do not apply the saved personal style
    # profile". The fallback language is part of that profile, and used to leak
    # into the prompt anyway whenever the thread gave no language signal.
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    cli.main(["profile", "set", "--language", "中文"])
    capsys.readouterr()

    import argparse

    from ai_reply_copilot.models import Message

    # An emoji-only thread gives no language signal, so the profile fallback is
    # the only thing that could set a language — which is exactly the leak.
    no_signal = [Message(text="👍", is_from_me=False, timestamp=None, sender="A")]

    def _args(ignore_profile):
        return argparse.Namespace(
            source="imessage", language=None, remember_language=False,
            ignore_profile=ignore_profile,
        )

    assert cli._resolve_language(_args(False), "10", no_signal)[0] == "中文"
    assert cli._resolve_language(_args(True), "10", no_signal)[0] is None


# --- learning your voice from your own sent messages ---------------------------


def test_cli_voice_learn_show_and_forget(chat_db, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    code = cli.main(["voice", "learn", "--db", str(chat_db), "--yes"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Learned from" in out

    assert cli.main(["voice", "show"]) == 0
    assert "Learned from" in capsys.readouterr().out

    assert cli.main(["voice", "forget"]) == 0
    assert "Forgot" in capsys.readouterr().out
    assert cli.main(["voice", "show"]) == 0
    assert "No voice learned yet" in capsys.readouterr().out


def test_cli_voice_learn_is_not_saved_without_consent(chat_db, capsys, tmp_path, monkeypatch):
    # Exemplars are uploaded with every future draft, so saving them is a
    # decision the user has to actually make.
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = cli.main(["voice", "learn", "--db", str(chat_db)])
    assert code == 1
    assert "Not saved" in capsys.readouterr().out
    assert cli.main(["voice", "show"]) == 0
    assert "No voice learned yet" in capsys.readouterr().out


def test_cli_voice_examples_zero_uploads_nothing_of_yours(chat_db, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    cli.main(["voice", "learn", "--db", str(chat_db), "--examples", "0", "--yes"])
    out = capsys.readouterr().out
    assert "statistics only" in out.lower()

    from ai_reply_copilot.storage import load_voice

    profile = load_voice()
    assert profile.sampled > 0      # it still learned
    assert profile.exemplars == []  # but keeps none of your messages


def test_cli_learned_voice_reaches_the_prompt(chat_db, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    cli.main(["voice", "learn", "--db", str(chat_db), "--yes"])
    capsys.readouterr()

    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["a"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    cli.main(["suggest", "10", "--db", str(chat_db), "--no-context"])
    assert "How the user actually writes" in fake.calls[-1][1]


def test_cli_ignore_profile_also_ignores_the_learned_voice(chat_db, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    cli.main(["voice", "learn", "--db", str(chat_db), "--yes"])
    capsys.readouterr()

    fake = FakeClient(json.dumps({"understanding": "x", "candidates": ["a"]}))
    monkeypatch.setattr(cli, "get_client", lambda provider, model: fake)
    cli.main(["suggest", "10", "--db", str(chat_db), "--no-context", "--ignore-profile"])
    assert "How the user actually writes" not in fake.calls[-1][1]
