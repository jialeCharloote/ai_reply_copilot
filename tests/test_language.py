"""Tests for per-conversation reply language."""

from __future__ import annotations

import pytest

from ai_reply_copilot.language import (
    CHINESE,
    ENGLISH,
    MIXED,
    detect_language,
    resolve_language,
)
from ai_reply_copilot.models import Message, render_context, unanswered_index
from ai_reply_copilot.storage import (
    get_conversation_language,
    set_conversation_language,
)


def _msgs(*pairs):
    return [
        Message(text=text, is_from_me=mine, timestamp=None, sender="Alex")
        for text, mine in pairs
    ]


# --- detection -----------------------------------------------------------------


def test_detects_an_english_work_conversation():
    assert detect_language(_msgs(("Can you review the deck before EOD?", False))) == ENGLISH


def test_detects_a_chinese_conversation():
    assert detect_language(_msgs(("今晚有空吗？一起吃饭", False))) == CHINESE


def test_chinese_with_english_loanwords_is_still_chinese():
    # A bilingual speaker keeps "deploy"/"deck" in English. Calling this "mixed"
    # would push the model into forced, unnatural code-switching.
    assert detect_language(_msgs(("这个 API migration 什么时候 deploy？", False))) == CHINESE
    assert detect_language(_msgs(("OK 我看下 deck，等下 sync 一下", False))) == CHINESE


def test_english_dominant_with_a_chinese_tail_is_mixed():
    assert detect_language(_msgs(("Sounds good, 谢谢", False))) == MIXED


def test_no_signal_from_emoji_only():
    assert detect_language(_msgs(("😂😂", False))) is None
    assert detect_language([]) is None


def test_detection_follows_the_other_side_not_your_own_old_language():
    # You used to write Chinese here; they have switched to English.
    assert detect_language(_msgs(("我等下回你", True), ("Any update on this?", False))) == ENGLISH


# --- resolution order ----------------------------------------------------------


def test_profile_language_does_not_override_the_conversation():
    # THE regression this whole feature exists for: a global "中文" must not turn
    # an English work channel's replies Chinese.
    english = _msgs(("Can you review the deck?", False))
    language, reason = resolve_language(english, profile_language=CHINESE)
    assert language == ENGLISH
    assert "detect" in reason


def test_profile_language_is_used_only_as_a_fallback():
    no_signal = _msgs(("😂", False))
    language, reason = resolve_language(no_signal, profile_language=CHINESE)
    assert language == CHINESE
    assert "profile" in reason


def test_a_locked_conversation_beats_detection():
    english = _msgs(("Can you review the deck?", False))
    language, _ = resolve_language(english, locked=CHINESE)
    assert language == CHINESE


def test_an_explicit_flag_beats_everything():
    english = _msgs(("Can you review the deck?", False))
    language, _ = resolve_language(english, explicit=MIXED, locked=CHINESE, profile_language=ENGLISH)
    assert language == MIXED


def test_nothing_to_go_on_leaves_it_to_the_model():
    language, _ = resolve_language(_msgs(("😂", False)))
    assert language is None


# --- the lock persists ---------------------------------------------------------


def test_conversation_language_lock_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    assert get_conversation_language("slack", "C1") is None
    set_conversation_language("slack", "C1", ENGLISH)
    assert get_conversation_language("slack", "C1") == ENGLISH
    # Locks are per conversation, not global.
    assert get_conversation_language("imessage", "42") is None
    set_conversation_language("imessage", "42", CHINESE)
    assert get_conversation_language("slack", "C1") == ENGLISH
    assert get_conversation_language("imessage", "42") == CHINESE
    # And can be cleared.
    set_conversation_language("slack", "C1", None)
    assert get_conversation_language("slack", "C1") is None


# --- what is still waiting on you ----------------------------------------------


def test_unanswered_index_points_at_the_pile_up():
    messages = _msgs(("Q1?", False), ("on it", True), ("Q2?", False), ("Q3?", False))
    assert unanswered_index(messages) == 2


def test_nothing_unanswered_when_you_spoke_last():
    assert unanswered_index(_msgs(("Q?", False), ("answered", True))) is None


def test_everything_is_unanswered_when_you_never_replied():
    assert unanswered_index(_msgs(("Q1?", False), ("Q2?", False))) == 0


def test_render_context_marks_what_arrived_since_your_last_reply():
    messages = _msgs(("Q1?", False), ("on it", True), ("Q2?", False))
    rendered = render_context(messages, mark_unanswered=True)
    assert "since your last reply" in rendered
    # The marker sits before Q2, not before Q1.
    assert rendered.index("since your last reply") > rendered.index("Q1?")
    assert rendered.index("since your last reply") < rendered.index("Q2?")


def test_render_context_unmarked_by_default():
    messages = _msgs(("Q1?", False), ("on it", True), ("Q2?", False))
    assert "since your last reply" not in render_context(messages)


def test_detection_falls_back_to_the_window_when_their_messages_carry_no_script():
    # They replied "👍"; your own recent lines are solidly Chinese. Conceding to
    # None here would drop the thread onto the profile fallback for no reason.
    convo = _msgs(("今晚吃什么？", True), ("我订了8点半", True), ("👍", False))
    assert detect_language(convo) == CHINESE


def test_still_no_signal_when_nobody_wrote_any_script():
    assert detect_language(_msgs(("👍", True), ("🎉", False))) is None


# --- the lock store must not eat your other conversations ----------------------


def test_a_corrupt_store_is_not_silently_overwritten(tmp_path, monkeypatch):
    from ai_reply_copilot.storage import ConversationStoreError, conversations_path

    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    set_conversation_language("slack", "C1", ENGLISH)
    set_conversation_language("imessage", "42", CHINESE)

    conversations_path().write_text("{ corrupt", encoding="utf-8")
    # Writing would otherwise replace the file with a single entry, destroying
    # every other conversation's lock.
    with pytest.raises(ConversationStoreError):
        set_conversation_language("slack", "C2", CHINESE)
    assert conversations_path().read_text(encoding="utf-8") == "{ corrupt"


def test_a_corrupt_store_still_reads_as_no_preferences(tmp_path, monkeypatch):
    from ai_reply_copilot.storage import conversations_path

    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    conversations_path().parent.mkdir(parents=True, exist_ok=True)
    conversations_path().write_text("{ corrupt", encoding="utf-8")
    # Reads must degrade, not crash a draft.
    assert get_conversation_language("slack", "C1") is None


def test_a_non_dict_entry_does_not_crash(tmp_path, monkeypatch):
    import json as _json

    from ai_reply_copilot.storage import conversations_path

    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    conversations_path().parent.mkdir(parents=True, exist_ok=True)
    conversations_path().write_text(_json.dumps({"slack:C1": "English"}), encoding="utf-8")
    assert get_conversation_language("slack", "C1") is None  # not an AttributeError
    set_conversation_language("slack", "C1", CHINESE)  # not a TypeError
    assert get_conversation_language("slack", "C1") == CHINESE
