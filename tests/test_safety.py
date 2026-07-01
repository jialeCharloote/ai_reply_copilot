"""Tests for sensitive-content detection."""

from __future__ import annotations

from ai_reply_copilot.safety import describe_warning, scan_sensitive


def test_detects_financial_english():
    assert "financial" in scan_sensitive("Can you send me your bank account number?")


def test_detects_financial_chinese():
    assert "financial" in scan_sensitive("把你的银行卡号发我一下")


def test_detects_medical():
    assert "medical" in scan_sensitive("The doctor changed my prescription")
    assert "medical" in scan_sensitive("医生说要手术")


def test_detects_legal():
    assert "legal" in scan_sensitive("My attorney says we should settle the lawsuit")
    assert "legal" in scan_sensitive("律师建议先仲裁")


def test_detects_work_confidential():
    assert "work-confidential" in scan_sensitive("This is confidential, internal only")
    assert "work-confidential" in scan_sensitive("这份内部资料别外传")


def test_detects_credentials():
    assert "credentials" in scan_sensitive("here's the api key: sk-123")
    assert "credentials" in scan_sensitive("验证码是 8842")


def test_case_insensitive():
    assert "work-confidential" in scan_sensitive("THIS IS CONFIDENTIAL")


def test_multiple_categories():
    found = scan_sensitive("My salary is confidential, don't share my password")
    assert set(found) >= {"financial", "work-confidential", "credentials"}


def test_clean_text_passes():
    assert scan_sensitive("Dinner at 7? Sounds good, see you there!") == []
    assert scan_sensitive("周六下午三点开会哈") == []


def test_describe_warning_mentions_categories():
    warning = describe_warning(["financial", "medical"])
    assert "financial, medical" in warning
    assert "cloud model" in warning
