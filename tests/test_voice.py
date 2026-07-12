"""Tests for learning the user's voice from their own sent messages.

The style questionnaire ("formality: casual") is the weakest part of the product.
The user has already written thousands of messages that sound exactly like them;
this learns from those instead. The privacy cost is real and is tested here:
exemplars ride along in *every* future prompt, including when replying to someone
else, so a secret in one of them would leak far beyond the conversation it came
from.
"""

from __future__ import annotations

from ai_reply_copilot.voice import (
    VoiceProfile,
    analyze_voice,
    describe_for_prompt,
    learn_voice,
    pick_exemplars,
)

_MINE = [
    "ok 我看下 deck，等下 sync 一下",
    "sounds good — 我周四之前给你确认",
    "can't tonight, 明天行吗",
    "已经 push 了，你 pull 一下就好",
    "let me check and revert",
]


# --- statistics stay local ------------------------------------------------------


def test_learns_that_you_write_short_lowercase_messages_without_periods():
    profile = analyze_voice(_MINE)
    assert profile.sampled == 5
    assert profile.median_chars < 40
    assert profile.ends_with_period_rate == 0.0
    assert profile.starts_lowercase_rate > 0.4
    described = profile.describe()
    assert "leaves off the final period" in described
    assert "starts messages in lowercase" in described


def test_notices_you_write_in_both_languages():
    described = analyze_voice(_MINE).describe()
    assert "Chinese and English" in described


def test_notices_heavy_emoji_use():
    profile = analyze_voice(["haha 😂", "ok 👍", "nice 🎉", "sure ✨"])
    assert profile.emoji_rate == 1.0
    assert "uses emoji often" in profile.describe()


def test_an_empty_history_learns_nothing():
    profile = analyze_voice([])
    assert profile.sampled == 0
    assert profile.describe() == ""
    assert describe_for_prompt(profile) == ""
    assert describe_for_prompt(None) == ""


# --- exemplars: the half that gets uploaded ------------------------------------


def test_a_secret_is_never_kept_as_an_exemplar():
    # THE privacy test. An exemplar rides along in every future prompt — including
    # when you reply to a different person — so a password memorialised here
    # leaks far beyond the conversation it came from.
    texts = _MINE + ["my password is hunter2", "api_key=sk-abc123def456ghi789jkl0"]
    exemplars = pick_exemplars(texts, limit=10)
    assert not any("hunter2" in e for e in exemplars)
    assert not any("sk-abc" in e for e in exemplars)


def test_a_salary_message_is_still_a_good_exemplar():
    # Only *blocking* categories are screened. A message about a salary is not a
    # secret, and dropping it would strip out the professional register we want.
    texts = ["Happy to discuss salary on Thursday — the range works for me."]
    assert pick_exemplars(texts) == texts


def test_links_and_one_word_replies_are_not_exemplars():
    texts = ["ok", "https://example.com/a/very/long/link", "sure"]
    assert pick_exemplars(texts) == []


def test_exemplars_are_deduplicated_and_capped():
    texts = ["thanks, will take a look tonight"] * 5 + ["got it, sending that over now"]
    assert len(pick_exemplars(texts, limit=8)) == 2
    assert len(pick_exemplars(_MINE * 10, limit=3)) == 3


def test_zero_examples_means_statistics_only():
    profile = learn_voice(_MINE, exemplar_limit=0)
    assert profile.sampled == 5          # still learned the stats
    assert profile.exemplars == []       # but nothing of yours is uploaded
    rendered = describe_for_prompt(profile)
    assert "How the user actually writes" in rendered
    assert "Real messages" not in rendered


# --- what the model is actually told --------------------------------------------


def test_the_prompt_block_carries_verbatim_examples():
    # Verbatim sentences teach voice far better than adjectives like "casual".
    rendered = describe_for_prompt(learn_voice(_MINE))
    assert "Real messages this user has sent" in rendered
    assert "ok 我看下 deck，等下 sync 一下" in rendered
    assert "do not copy the content" in rendered


def test_a_profile_round_trips_through_storage(tmp_path, monkeypatch):
    from ai_reply_copilot.storage import forget_voice, load_voice, save_voice

    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    assert load_voice() is None
    save_voice(learn_voice(_MINE))
    loaded = load_voice()
    assert isinstance(loaded, VoiceProfile)
    assert loaded.sampled == 5
    assert loaded.exemplars
    assert forget_voice() is True
    assert load_voice() is None
    assert forget_voice() is False


# --- adversarial: what an exemplar must never carry ----------------------------

import pytest  # noqa: E402

# Each of these would otherwise be memorialised into EVERY future prompt — sent
# to the cloud even when replying to a completely different person. Selecting an
# exemplar is a selection problem, not a detection problem: there are thousands
# of candidate messages and only eight slots, so anything that merely smells like
# a secret is discarded.
SECRETS = [
    "AKIAIOSFODNN7EXAMPLE is the access key for staging",   # AWS key id
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",             # AWS secret
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456",    # JWT
    "ghp_abcdefghijklmnopqrstuvwxyz1234567890",             # GitHub PAT
    "登录用 admin / Passw0rd!23 就行",                        # password, no keyword
    "我的 pin 是 4821 你自己看",                              # PIN, Chinese separator
    "wifi 是 CoffeeShop2024 你连一下",                        # wifi password
    "Authorization: Bearer abc123def456ghi789",             # bearer token
    "card 4111111111111111 exp 12/28",                      # card, no spaces
    "账号是 6222021234567890123",                            # bank account
    "验证码 738291 别告诉别人",                               # OTP
]


@pytest.mark.parametrize("secret", SECRETS)
def test_no_secret_is_ever_kept_as_an_exemplar(secret):
    assert pick_exemplars([secret]) == []


# And the flip side: being paranoid must not throw away the voice itself.
VOICE = [
    "ok 我看下 deck，等下 sync 一下",
    "sounds good — 我周四之前给你确认",
    "can't tonight, 明天行吗",
    "已经 push 了，你 pull 一下就好",
    "Happy to discuss salary on Thursday — the range works for me.",
    "哈哈哈那我先撤了，明天见",
    "这个 API migration 什么时候 deploy？",
    "Let's sync at 3pm, I'll bring the deck",
]


def test_the_paranoia_does_not_eat_the_voice():
    assert pick_exemplars(VOICE, limit=20) == VOICE


# --- the leaks a first pass missed ---------------------------------------------


HYPHENATED = [
    "Tulip-Garden-88",                      # wifi/door code, digits + hyphens
    "the front door is Blue-Sky-Rain",      # capitalised code phrase, no digits
]


@pytest.mark.parametrize("secret", HYPHENATED)
def test_hyphenated_codes_are_not_exemplars(secret):
    # The token/password patterns excluded `-` from their character classes, so
    # every hyphenated passphrase walked straight through.
    assert pick_exemplars([secret]) == []


def test_a_bare_secret_is_caught_by_what_it_was_answering():
    # The structural hole: "sunshinecoast" carries no marker of its own. The only
    # thing that identifies it as a secret is the question it answers, which lives
    # in the *other* person's message — so screening the user's message alone is
    # blind to it by construction.
    from ai_reply_copilot.imessage import SentSample

    asked = SentSample(text="sunshinecoast", prompt_text="hey what's the wifi password?")
    assert pick_exemplars([asked]) == []
    asked_zh = SentSample(text="hunter2isgreat", prompt_text="密码是多少？")
    assert pick_exemplars([asked_zh]) == []

    # …and the same message is a perfectly good exemplar when it answers something
    # innocuous. The context is what decides, not the words.
    innocuous = SentSample(text="sunshinecoast", prompt_text="what should we call the release?")
    assert pick_exemplars([innocuous]) == ["sunshinecoast"]


def test_common_professional_words_are_not_treated_as_secrets():
    # `code`, `key`, `account` and `login` are four of the commonest words in
    # professional English. Rejecting on them silently biased the exemplar pool
    # away from exactly the register this product exists to imitate.
    work = [
        "I'll review the code tomorrow morning, sounds good",
        "the key thing is we ship before the demo",
        "let me check my account and get back to you",
        "can you send me the login flow mock?",
        "I think the key insight is that users don't read",
        "sounds good, let's sync at 3 and lock the code freeze",
    ]
    assert pick_exemplars(work, limit=20) == work


# --- a stale or corrupt profile must not keep leaking ---------------------------


def test_a_profile_from_an_older_screen_is_ignored(tmp_path, monkeypatch):
    import json as _json

    from ai_reply_copilot.storage import load_voice, voice_path

    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    voice_path().parent.mkdir(parents=True, exist_ok=True)
    # No version field: written before the current (stricter) screen existed, so
    # its exemplars were vetted under weaker rules and cannot be trusted.
    voice_path().write_text(
        _json.dumps({"sampled": 5, "exemplars": ["my password is hunter2"]}), encoding="utf-8"
    )
    assert load_voice() is None  # force a re-learn


def test_exemplars_are_re_screened_on_load(tmp_path, monkeypatch):
    import json as _json

    from ai_reply_copilot.storage import load_voice, voice_path
    from ai_reply_copilot.voice import VOICE_SCHEMA_VERSION

    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    voice_path().parent.mkdir(parents=True, exist_ok=True)
    voice_path().write_text(
        _json.dumps({
            "version": VOICE_SCHEMA_VERSION,
            "sampled": 5,
            "exemplars": ["Tulip-Garden-88", "ok 我看下 deck，等下 sync 一下"],
        }),
        encoding="utf-8",
    )
    profile = load_voice()
    # Screening happens at learn time, so a secret that slipped past an older rule
    # would otherwise keep uploading forever. Re-screen on every load.
    assert profile.exemplars == ["ok 我看下 deck，等下 sync 一下"]


def test_a_corrupt_profile_degrades_instead_of_crashing_every_draft(tmp_path, monkeypatch):
    import json as _json

    from ai_reply_copilot.storage import load_voice, voice_path
    from ai_reply_copilot.voice import VOICE_SCHEMA_VERSION, describe_for_prompt

    monkeypatch.setenv("AI_REPLY_COPILOT_HOME", str(tmp_path))
    voice_path().parent.mkdir(parents=True, exist_ok=True)
    voice_path().write_text(
        _json.dumps({
            "version": VOICE_SCHEMA_VERSION,
            "sampled": 5,
            "emoji_rate": "high",       # wrong type: used to raise inside describe()
            "exemplars": "hunter2",     # a string, not a list: used to be iterated per character
        }),
        encoding="utf-8",
    )
    profile = load_voice()
    assert profile.emoji_rate == 0.0
    assert profile.exemplars == []
    describe_for_prompt(profile)  # must not raise on any draft path
