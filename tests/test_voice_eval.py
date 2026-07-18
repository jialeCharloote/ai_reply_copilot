"""Tests for the style-fidelity eval: do drafts actually sound like the user?

`charla voice learn` measures how the user writes, but nothing measured whether
generation honours what was learned — the only signal was `feedback not_like_me`,
one anecdote at a time. The eval closes that loop with the same counters
`analyze_voice` uses, so what is pinned here is *attribution*: a set of drafts
that deviates on one dimension must be flagged on that dimension and no other.
"""

from __future__ import annotations

import pytest

from ai_reply_copilot import cli
from ai_reply_copilot.voice import VoiceProfile, analyze_voice
from ai_reply_copilot.voice_eval import (
    DIMENSION_NAMES,
    compare_drafts,
    evaluate,
    format_report,
    load_eval_set,
)

# The same bilingual persona the bundled fixture uses: short, code-switched,
# lowercase English starts, no emoji, no final periods. Entirely invented.
_MINE = [
    "ok 我下午看下那个 proposal",
    "这个 API 什么时候 deploy?",
    "还没 merge，CI 那边有点问题",
    "嗯嗯 可以，那就这么定了",
    "figma 链接发我下?",
    "哈哈 好，那我先去开会了",
    "sounds good, will confirm by thursday",
    "can't tonight, tomorrow works?",
    "on it, update by tonight",
    "let me check and revert",
    "just shipped it, take a look",
    "ok deal",
]


def _profile() -> VoiceProfile:
    return analyze_voice(_MINE)


# --- the eval cannot disagree with the learner ----------------------------------


def test_the_users_own_messages_pass_their_own_profile():
    # Both sides of the comparison run through the same analyze_voice, so the
    # messages a profile was learned from must score as a perfect match. If this
    # ever fails, the eval has grown its own statistics and can drift.
    report = compare_drafts(_profile(), _MINE)
    assert report.faithful
    assert report.off_dimensions == []


def test_every_profile_dimension_is_scored():
    report = compare_drafts(_profile(), _MINE)
    assert [c.dimension for c in report.checks] == DIMENSION_NAMES


# --- deviation on one dimension is attributed to that dimension, not a vibe ----


def test_emoji_happy_drafts_are_flagged_on_emoji_and_nothing_else():
    drafts = [text + " 😊" for text in _MINE]
    report = compare_drafts(_profile(), drafts)
    assert report.off_dimensions == ["emoji_rate"]


def test_padded_out_drafts_are_flagged_on_length_and_nothing_else():
    # Each draft repeats its own message, so every containment rate (emoji,
    # question, exclamation...) and the language mix stay identical — only the
    # length moves. The flag must move with it and with nothing else.
    drafts = [f"{text}，{text}，{text}，{text}" for text in _MINE]
    report = compare_drafts(_profile(), drafts)
    assert report.off_dimensions == ["median_chars"]
    length = next(c for c in report.checks if c.dimension == "median_chars")
    assert "longer" in length.detail


def test_answering_a_bilingual_texter_in_pure_english_is_flagged():
    drafts = [
        "sure, will take a look tomorrow",
        "when is this going out?",
        "not merged yet, ci is being flaky",
        "Sounds good, thursday works",
        "on it, update by tonight",
        "Let me check and revert",
    ]
    report = compare_drafts(_profile(), drafts)
    assert report.off_dimensions == ["chinese_rate"]


def test_a_code_switched_draft_still_counts_as_chinese():
    # "这个 API 什么时候 deploy" keeps its loanwords in English and is still a
    # Chinese sentence. If the eval called it English, it would tell a bilingual
    # user their perfectly natural drafts are in the wrong language.
    drafts = [
        "这个 API 什么时候 deploy?",
        "还没 merge，CI 那边有点问题",
        "ok 我下午看下那个 proposal",
        "嗯嗯 可以，那就这么定了",
        "figma 链接发我下?",
        "哈哈 好，那我先去开会了",
    ]
    report = compare_drafts(_profile(), drafts)
    chinese = next(c for c in report.checks if c.dimension == "chinese_rate")
    assert chinese.observed == 1.0


def test_small_wobble_is_not_drift():
    # Rates over five drafts move in steps of 0.2 — one message. A tolerance
    # that flags that flags sampling noise, and the report stops being trusted.
    drafts = [
        "ok 我明天上午过一遍 timeline",
        "这个 branch 今晚能 merge 吗?",
        "sounds good, i'll send the doc first",
        "还没定，等 marketing 那边 confirm",
        "on it, will update before eod",
    ]
    report = compare_drafts(_profile(), drafts)
    assert report.faithful


# --- the report speaks in numbers, not adjectives -------------------------------


def test_the_report_says_how_far_off_not_just_that_it_is_off():
    drafts = ["Thank you so much! 😊 I will certainly review the entire document tonight."] * 5
    report = compare_drafts(_profile(), drafts)
    emoji = next(c for c in report.checks if c.dimension == "emoji_rate")
    assert emoji.off
    assert "1.00" in emoji.detail and "0.00" in emoji.detail
    length = next(c for c in report.checks if c.dimension == "median_chars")
    assert "×" in length.detail  # a magnitude, not "too long"


# --- the bundled fixture is a self-checking eval --------------------------------


def test_the_bundled_fixture_detects_exactly_what_its_labels_claim():
    result = evaluate(load_eval_set())
    assert result.agrees
    by_name = {outcome.name: outcome for outcome in result.outcomes}
    assert by_name["faithful"].report.faithful
    assert "emoji_rate" in by_name["assistant-flavored"].report.off_dimensions
    assert by_name["wrong-language"].report.off_dimensions == ["chinese_rate"]


def test_a_missed_or_extra_flag_reads_as_disagreement():
    eval_set = load_eval_set()
    # Claim the assistant-flavored set is fine: every real flag becomes
    # "unexpected" and the eval must refuse to agree with the labels.
    for case in eval_set["draft_sets"]:
        case["expect_off"] = []
    result = evaluate(eval_set)
    assert not result.agrees
    rendered = format_report(result)
    assert "flagged beyond the labels" in rendered
    assert "DISAGREE" in rendered


# --- degenerate inputs fail loudly, not quietly ---------------------------------


def test_scoring_without_a_profile_or_without_drafts_is_an_error():
    with pytest.raises(ValueError):
        compare_drafts(VoiceProfile(), ["hi there, how are you"])
    with pytest.raises(ValueError):
        compare_drafts(_profile(), [])


def test_a_missing_fixture_is_an_error_not_a_traceback():
    with pytest.raises(ValueError):
        load_eval_set("/nowhere/voice_eval.json")


# --- the CLI runs the whole thing offline ---------------------------------------


def test_cli_voice_eval_runs_offline_and_prints_attribution(capsys):
    code = cli.main(["voice", "eval"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Voice fidelity" in out
    assert "assistant-flavored" in out
    assert "≈" in out  # magnitudes, not verdicts
    assert "every labelled deviation was caught" in out


def test_cli_voice_eval_missing_fixture_exits_2(capsys):
    code = cli.main(["voice", "eval", "--fixture", "/nowhere/voice_eval.json"])
    err = capsys.readouterr().err
    assert code == 2
    assert "error:" in err


# --- measurement mode: the saved voice vs the drafts the product generated ------
# (the conftest autouse fixture points AI_REPLY_COPILOT_HOME at a fresh temp
# directory, so these compose their own state without touching a real home)


def _save_my_voice():
    from ai_reply_copilot.storage import save_voice

    save_voice(analyze_voice(_MINE))


def test_generating_replies_feeds_the_draft_pool():
    # The log lives in generate_replies — the one choke point every surface
    # (suggest, reply flow, Slack app) passes through — so the eval pool grows
    # no matter which front-end drafted.
    import json as _json

    from ai_reply_copilot.generate import generate_replies
    from ai_reply_copilot.llm import FakeClient
    from ai_reply_copilot.models import Message
    from ai_reply_copilot.storage import load_recent_drafts

    fake = FakeClient(
        _json.dumps({"understanding": "u", "candidates": ["ok 我看下", "sounds good"]})
    )
    generate_replies([Message(text="hi", is_from_me=False, timestamp=None, sender=None)], fake)
    assert load_recent_drafts() == ["ok 我看下", "sounds good"]


def test_against_saved_scores_the_logged_drafts(capsys):
    from ai_reply_copilot.storage import record_drafts

    _save_my_voice()
    record_drafts(
        [
            "ok 我明天上午过一遍 timeline",
            "这个 branch 今晚能 merge 吗?",
            "sounds good, i'll send the doc first",
            "还没定，等 marketing 那边 confirm",
            "on it, will update before eod",
        ]
    )
    code = cli.main(["voice", "eval", "--against-saved"])
    out = capsys.readouterr().out
    assert code == 0
    assert "your last 5 drafts" in out
    assert "statistically consistent" in out  # honest wording, not "sounds like you"


def test_against_saved_flags_off_voice_drafts_and_exits_1(capsys):
    from ai_reply_copilot.storage import record_drafts

    _save_my_voice()
    record_drafts(
        ["Thank you so much! 😊 I will certainly review the entire document tonight."] * 5
    )
    code = cli.main(["voice", "eval", "--against-saved"])
    out = capsys.readouterr().out
    assert code == 1
    assert "off on:" in out
    assert "emoji" in out


def test_against_saved_warns_when_the_pool_is_too_small(capsys):
    from ai_reply_copilot.storage import record_drafts

    _save_my_voice()
    record_drafts(["ok 我看下 proposal", "sounds good, will do"])
    cli.main(["voice", "eval", "--against-saved"])
    out = capsys.readouterr().out
    assert "only 2 drafts" in out


# --- per-candidate hints: flag the off-voice draft BEFORE the user picks it ----


def test_an_ai_flavored_candidate_is_hinted_with_specifics():
    from ai_reply_copilot.voice_eval import off_voice_hints

    hints = off_voice_hints(
        _profile(),
        "Thank you so much for the update! 😊 I will review the entire document "
        "carefully tonight and send over my detailed thoughts tomorrow morning.",
    )
    joined = " ".join(hints)
    assert "× longer" in joined
    assert "emoji" in joined
    assert "exclamation" in joined
    assert "period" in joined


def test_a_faithful_candidate_gets_no_hint():
    from ai_reply_copilot.voice_eval import off_voice_hints

    assert off_voice_hints(_profile(), "ok 我明天上午过一遍 timeline") == []
    assert off_voice_hints(None, "anything at all") == []
    assert off_voice_hints(VoiceProfile(), "anything at all") == []


def test_hints_only_fire_on_habits_the_user_never_has():
    # Someone who uses emoji half the time gets no emoji nag — a hint that
    # fires on normal variation trains the user to ignore all of them.
    from ai_reply_copilot.voice_eval import off_voice_hints

    emoji_user = analyze_voice(["haha 😂 好", "ok 👍 收到", "nice 🎉 走起", "sure ✨ 明天见"])
    assert off_voice_hints(emoji_user, "sounds good 😊") == []


def test_a_moderately_longer_draft_is_tolerated_but_a_screenful_is_hinted():
    # Intent can legitimately double a draft — a formal decline runs long. The
    # per-candidate bar is "reads as a different person", far looser than the
    # pool-level check.
    from ai_reply_copilot.voice_eval import off_voice_hints

    profile = _profile()  # median ~19 chars
    assert off_voice_hints(profile, "ok 我看下, 不行的话我们就先按原来的 plan 走") == []
    assert off_voice_hints(
        profile,
        "ok 我看下, 不行的话我们就先按原来的 plan 走, 然后我再和 design 那边把新的 timeline 对一遍, 对完发群里",
    ) != []


def test_language_is_deliberately_never_hinted_per_candidate():
    # The reply language follows the conversation: an English work channel gets
    # English drafts from a half-Chinese texter because resolve_language said
    # so. A per-candidate language nag would fight that decision on every draft.
    from ai_reply_copilot.voice_eval import off_voice_hints

    assert off_voice_hints(_profile(), "sounds good, will send it tomorrow") == []


def test_cli_suggest_flags_the_off_voice_candidate(chat_db, capsys, monkeypatch):
    import json as _json

    _save_my_voice()
    fake_reply = {
        "understanding": "Alex is asking about tonight.",
        "candidates": [
            "ok 我看下 明天答复你",
            "Thank you so much for asking! 😊 I will absolutely confirm the timing "
            "with everyone involved and circle back to you tomorrow morning.",
        ],
    }
    from ai_reply_copilot.llm import FakeClient

    monkeypatch.setattr(
        cli, "get_client", lambda provider, model: FakeClient(_json.dumps(fake_reply))
    )
    code = cli.main(["suggest", "10", "--db", str(chat_db)])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("⚠") >= 1
    # The faithful candidate is not nagged; the AI-flavored one is.
    faithful_line = next(line for line in out.splitlines() if "ok 我看下" in line)
    assert "⚠" not in faithful_line
    assert "you almost never use them" in out

    # --ignore-profile suppresses the voice AND the hints about it.
    code = cli.main(["suggest", "10", "--db", str(chat_db), "--ignore-profile"])
    out = capsys.readouterr().out
    assert code == 0
    assert "⚠" not in out


def test_cli_suggest_json_carries_hints_aligned_with_candidates(chat_db, capsys, monkeypatch):
    import json as _json

    _save_my_voice()
    fake_reply = {
        "understanding": "u",
        "candidates": ["ok 我看下", "Wonderful news! 😊 Thank you so much for letting me know."],
    }
    from ai_reply_copilot.llm import FakeClient

    monkeypatch.setattr(
        cli, "get_client", lambda provider, model: FakeClient(_json.dumps(fake_reply))
    )
    code = cli.main(["suggest", "10", "--db", str(chat_db), "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(payload["voice_hints"]) == len(payload["candidates"])
    assert payload["voice_hints"][0] == []
    assert payload["voice_hints"][1] != []


def test_reply_flow_prints_the_hint_before_the_pick():
    import json as _json

    from ai_reply_copilot.flow import run_reply_flow
    from ai_reply_copilot.llm import FakeClient
    from ai_reply_copilot.models import Message
    from ai_reply_copilot.send import SendResult

    fake = FakeClient(
        _json.dumps(
            {
                "understanding": "u",
                "candidates": ["ok 我看下", "Absolutely wonderful! 😊 Thank you so much!"],
            }
        )
    )
    out = []
    answers = iter(["q"])
    run_reply_flow(
        [Message(text="dinner?", is_from_me=False, timestamp=None, sender="Alex")],
        fake,
        lambda text, dry_run: SendResult("imessage", "+1555", text, dry_run, "ok"),
        voice_profile=_profile(),
        prompt=lambda _msg: next(answers),
        output=out.append,
    )
    rendered = "\n".join(out)
    # The hint sits under the off-voice candidate, in the pick list itself —
    # after the user has chosen is too late.
    assert rendered.index("2. Absolutely wonderful") < rendered.index("⚠")
    assert "⚠" not in rendered[: rendered.index("2. Absolutely")]  # faithful one unflagged


def test_against_saved_without_a_voice_or_without_drafts_says_what_to_run(capsys):
    code = cli.main(["voice", "eval", "--against-saved"])
    err = capsys.readouterr().err
    assert code == 2
    assert "charla voice learn" in err

    _save_my_voice()
    code = cli.main(["voice", "eval", "--against-saved"])
    err = capsys.readouterr().err
    assert code == 2
    assert "charla suggest" in err
