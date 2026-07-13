"""Tests for sensitive-content detection, split by severity.

The central design claim: an actual secret stops you; a sensitive *topic* does
not. Charla's first wedge is professional replies — recruiters, offers, clients —
so a gate that fires on "salary" fires on nearly every conversation the product
exists for, and users learn to click straight through it.
"""

from __future__ import annotations

from ai_reply_copilot.safety import (
    describe_notice,
    describe_warning,
    scan_blocking,
    scan_notice,
    scan_sensitive,
)


# --- the professional wedge must NOT be interrupted -----------------------------


def test_a_salary_negotiation_is_a_notice_not_a_block():
    text = "The offer is $180k base plus equity. Can we discuss salary on Thursday?"
    assert scan_blocking(text) == []          # does not interrupt
    assert "financial" in scan_notice(text)   # but is reported


def test_a_chinese_offer_conversation_is_a_notice_not_a_block():
    text = "这个 offer 的薪资和期权大概什么水平？"
    assert scan_blocking(text) == []
    assert "financial" in scan_notice(text)


def test_a_contract_discussion_is_a_notice_not_a_block():
    text = "Legal is reviewing the contract dispute; our attorney will get back to you."
    assert scan_blocking(text) == []
    assert "legal" in scan_notice(text)


def test_a_bank_account_topic_is_a_notice_not_a_block():
    # Talking *about* a bank account is not the same as pasting the number.
    text = "Can you send me your bank account details when you get a chance?"
    assert scan_blocking(text) == []
    assert "financial" in scan_notice(text)


def test_an_ordinary_work_thread_trips_nothing():
    text = "Can you review the deck before EOD? Also ok to present Thursday?"
    assert scan_blocking(text) == []
    assert scan_notice(text) == []


# --- an actual secret DOES stop you --------------------------------------------


def test_a_plaintext_password_blocks():
    assert "credentials" in scan_blocking("the password is hunter2, log in and check")


def test_a_chinese_password_blocks():
    assert "credentials" in scan_blocking("密码是 abc123，你自己登进去看")


def test_an_api_key_assignment_blocks():
    assert "credentials" in scan_blocking("use api_key=sk-abc123def456ghi789jkl012 for staging")


def test_a_slack_token_blocks():
    assert "credentials" in scan_blocking("here's the token xoxb-1234567890-abcdefghij")


def test_a_private_key_block_blocks():
    assert "credentials" in scan_blocking("-----BEGIN RSA PRIVATE KEY-----\nMIIE...")


def test_a_card_number_blocks():
    assert "financial-id" in scan_blocking("card is 4111 1111 1111 1111, exp 12/28")


def test_an_ssn_blocks():
    assert "financial-id" in scan_blocking("my ssn is 123-45-6789")


def test_an_explicit_do_not_share_blocks():
    assert "confidential-marked" in scan_blocking("这个别外传，内部资料")
    assert "confidential-marked" in scan_blocking("Do not share this outside the team")


# --- word boundaries: the false positive that made the old gate noise ----------


def test_passwordless_does_not_fire():
    # The old substring scan matched "password" inside "passwordless".
    assert scan_blocking("we're moving to passwordless login next quarter") == []
    assert scan_blocking("The passwordless flow ships Friday") == []


def test_a_bare_nine_digit_number_is_not_a_routing_number():
    # Only a *labelled* routing number counts; bare digits are far too common.
    assert scan_blocking("the ticket id is 123456789") == []
    assert "financial-id" in scan_blocking("routing number: 021000021")


# --- reporting -----------------------------------------------------------------


def test_scan_sensitive_reports_everything_blocking_first():
    found = scan_sensitive("my password is hunter2 and let's discuss salary")
    assert found[0] == "credentials"  # blocking categories come first
    assert "financial" in found


def test_scan_sensitive_does_not_duplicate_a_category():
    assert scan_sensitive("password: hunter2").count("credentials") == 1


def test_the_two_messages_read_differently():
    # The block message warns; the notice merely informs.
    assert "cloud model" in describe_warning(["credentials"])
    assert "touches on" in describe_notice(["financial"])


def test_empty_text_is_clean():
    assert scan_blocking("") == []
    assert scan_notice("") == []
    assert scan_sensitive("") == []


# --- long numbers: the card regex must not eat ordinary work messages ----------


def test_a_real_card_number_blocks_even_without_spaces():
    # Luhn-valid test numbers.
    assert "financial-id" in scan_blocking("card 4111111111111111 exp 12/28")
    assert "financial-id" in scan_blocking("5500-0000-0000-0004")


def test_ordinary_long_numbers_do_not_block():
    # A bare "13-19 digits" rule blocked order ids, invoices, tickets and build
    # numbers — exactly the professional noise this module exists to avoid. The
    # Luhn check is what separates a real card from a long id.
    for text in (
        "The order id is 1234567890123",
        "invoice 8801234567890 was paid",
        "ticket #1234567890123456 is closed",
        "the build number is 20260712-1430-5567",
        "call me at 415 555 0123 tomorrow",
        "我的手机号是 13812345678",
    ):
        assert scan_blocking(text) == [], text


# --- secret shapes a vocabulary scanner misses ---------------------------------


def test_token_shapes_block():
    assert "credentials" in scan_blocking("AKIAIOSFODNN7EXAMPLE is the access key")
    assert "credentials" in scan_blocking("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456")
    assert "credentials" in scan_blocking("Authorization: Bearer abc123def456ghi789")


def test_a_chinese_separator_still_counts():
    # "pin 是 4821" — the value is what makes it a secret, and this user writes
    # the separator in Chinese.
    assert "credentials" in scan_blocking("我的 pin 是 4821")
    assert "credentials" in scan_blocking("wifi 是 CoffeeShop2024")


def test_a_credential_word_without_a_secret_value_does_not_block():
    # The bug this replaces: requiring merely "\S+" after the keyword meant
    # "slow" counted as a value, so ordinary sentences hard-blocked. A value has
    # to *look* like a secret. Every line below is a normal work message.
    for text in (
        "the wifi is slow",
        "the wifi keeps dropping in this room",
        "the token is expired, I'll refresh it",
        "the secret is out",
        "pin is 5 minutes away",
        "can you pin the message in the channel?",
        "Password reset emails are broken",
        "I forgot my password",
        "我忘记密码了",
        "the login flow is broken",
    ):
        assert scan_blocking(text) == [], text


def test_a_credential_word_carrying_a_value_still_blocks():
    for text in (
        "the password is hunter2",
        "密码是 abc123",
        "wifi 是 CoffeeShop2024",
        "我的 pin 是 4821",
        "验证码 738291",
        "the wifi password is Blue-Sky-Rain",
    ):
        assert "credentials" in scan_blocking(text), text


def test_an_alphabetic_password_blocks():
    """The hole the digit/symbol rule left wide open.

    Requiring a secret value to carry a digit or a symbol meant every purely
    alphabetic password walked straight through the one hard gate in the product
    — and diceware passphrases, wifi codes and door codes are the secrets people
    actually share over iMessage and Slack. `voice.py` even uses `sunshinecoast`
    as its canonical example of a secret, while the live-context gate ignored it.
    """
    for text in (
        "the wifi password is correcthorsebattery",
        "password: bluemountain",
        "my password is opensesame",
        "passcode: bluemoon",
        "wifi 密码是 sunshinecoast",
        "门禁密码是 sunshinecoast",
        "the passphrase is purple giraffe",
    ):
        assert "credentials" in scan_blocking(text), text


def test_predicates_after_a_credential_word_still_do_not_block():
    """The other half of the same rule: over-blocking is how a gate gets ignored.

    Closing the alphabetic hole must not turn every "the wifi is slow" into a
    hard stop — a gate that trips on ordinary work talk trains the user to click
    through it, and then it protects nothing.
    """
    for text in (
        "credentials are stored in 1Password",
        "the api key is missing from the config",
        "2fa is required for the admin panel",
        "wifi is down again",
        "the pin is not working",
        "my password is wrong",
        "the token is expired",
        "the login is broken",
    ):
        assert scan_blocking(text) == [], text


def test_offer_is_matched_as_a_word_not_a_substring():
    # "offer" sat in the CJK list, which matches case-sensitive substrings: it
    # missed "Offer accepted!" and fired inside unrelated uses of the verb.
    assert "work-confidential" in scan_notice("Offer accepted!")
    assert "work-confidential" in scan_notice("did the offer come through?")
