"""Tests for local-first style profile and feedback storage."""

from __future__ import annotations

import pytest

from ai_reply_copilot import storage
from ai_reply_copilot.prompts import StyleProfile


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    return tmp_path


def test_load_profile_missing_returns_none(home):
    assert storage.load_style_profile() is None


def test_save_and_load_profile(home):
    profile = StyleProfile(
        formality="casual", directness="direct", use_emoji=True, language="中文"
    )
    storage.save_style_profile(profile)
    loaded = storage.load_style_profile()
    assert loaded.formality == "casual"
    assert loaded.use_emoji is True
    assert loaded.language == "中文"


def test_load_profile_ignores_unknown_keys(home):
    storage.profile_path().write_text('{"formality": "formal", "junk": 1}', encoding="utf-8")
    loaded = storage.load_style_profile()
    assert loaded.formality == "formal"


def test_load_profile_bad_json_returns_none(home):
    storage.profile_path().write_text("not json", encoding="utf-8")
    assert storage.load_style_profile() is None


def test_record_and_load_feedback(home):
    storage.record_feedback("useful", "Sounds good!", source="imessage", target="10")
    storage.record_feedback("wrong_tone", "Nope", source="slack", target="C1")
    entries = storage.load_feedback()
    assert len(entries) == 2
    assert entries[0]["rating"] == "useful"
    assert entries[1]["target"] == "C1"
    assert "timestamp" in entries[0]


def test_record_feedback_invalid_rating(home):
    with pytest.raises(ValueError):
        storage.record_feedback("bad", "x")


# --- the draft log: what `voice eval --against-saved` scores --------------------


def test_drafts_round_trip_oldest_first(home):
    storage.record_drafts(["ok 我看下", "sounds good"], source="imessage", target="10")
    storage.record_drafts(["on it"])
    assert storage.load_recent_drafts() == ["ok 我看下", "sounds good", "on it"]
    assert storage.load_recent_drafts(limit=2) == ["sounds good", "on it"]


def test_empty_or_blank_candidates_are_not_logged(home):
    assert storage.record_drafts(["", "   "]) is None
    assert not storage.drafts_path().exists()
    assert storage.load_recent_drafts() == []


def test_a_corrupt_line_does_not_take_the_eval_down(home):
    storage.record_drafts(["fine draft"])
    with storage.drafts_path().open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    storage.record_drafts(["later draft"])
    assert storage.load_recent_drafts() == ["fine draft", "later draft"]


def test_the_draft_log_does_not_grow_forever(home):
    # Drafts are derived from private conversations; an append-only log would
    # quietly accumulate months of them on disk.
    for index in range(0, storage._DRAFTS_KEEP + 40, 4):
        storage.record_drafts([f"draft number {index + offset}" for offset in range(4)])
    lines = storage.drafts_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) <= storage._DRAFTS_KEEP
    # ...and it is the oldest drafts that were dropped, not the newest.
    assert storage.load_recent_drafts(limit=1) == [f"draft number {storage._DRAFTS_KEEP + 39}"]
