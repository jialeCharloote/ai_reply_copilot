"""Sensitive-content detection, split by severity (PRD P0).

Before conversation context is sent to a cloud LLM, scan it locally and decide
how loudly to react. The split matters more than the word list:

- **BLOCK** — an actual secret or identifier is present (a password, an API key,
  a card/SSN/IBAN number). Stop and make the user confirm. These are rare, so
  the prompt stays meaningful.
- **NOTICE** — the conversation is merely *about* a sensitive topic (a salary, an
  offer letter, a contract, a diagnosis). Say so, but do not interrupt.

The earlier version blocked on topics, which was self-defeating: Charla's first
wedge is professional replies — recruiters, clients, offers — so ``salary`` and
``offer letter`` fire on precisely the conversations the product exists to serve.
A gate that trips every time trains the user to click through it, and then it
protects nothing.

Matching is a local heuristic, not a classifier: ASCII terms are matched on word
boundaries (so ``password`` does not fire inside ``passwordless``), CJK terms by
substring (Chinese has no word boundaries), and identifiers by shape.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

BLOCK = "block"
NOTICE = "notice"

# --- things that are actually secret -------------------------------------------

# Word-boundary terms. `\b` works for ASCII; CJK terms are handled below.
_BLOCK_TERMS: Dict[str, Tuple[str, ...]] = {
    "credentials": (
        "password", "passcode", "api key", "apikey", "secret key", "access token",
        "auth token", "private key", "ssh key", "one-time code", "2fa code",
        "seed phrase", "recovery phrase",
    ),
    "confidential-marked": (
        "do not share", "don't share", "not for distribution", "internal only",
        "trade secret", "under nda",
    ),
}

_BLOCK_CJK: Dict[str, Tuple[str, ...]] = {
    "credentials": ("密码", "验证码", "秘钥", "密钥", "私钥", "助记词", "口令", "暗号"),
    "confidential-marked": ("别外传", "不要外传", "请勿外传", "内部资料", "机密"),
}

# Identifiers matched by *shape*, not by vocabulary. This is what actually makes
# a message dangerous to upload.
_CARD_CANDIDATE = re.compile(r"(?<![\d-])(?:\d[ -]?){13,19}(?![\d-])")


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — what tells a real card number from an order id.

    A bare "13-19 digits" rule blocks invoice numbers, ticket ids and build
    numbers, which is exactly the noise this module exists to avoid. Real card
    numbers carry a check digit; arbitrary long numbers pass only by luck.
    """
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _has_card_number(text: str) -> bool:
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"[ -]", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return True
    return False


_BLOCK_PATTERNS: Dict[str, Tuple[re.Pattern, ...]] = {
    "financial-id": (
        # US SSN
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        # IBAN
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
        # US routing number, only when labelled (9 bare digits are too common).
        re.compile(r"\brouting\s*(?:number|no\.?|#)?\s*:?\s*\d{9}\b", re.I),
    ),
    "credentials": (
        # "password: hunter2", "api_key=sk-...", "token is abc123"
        # "password: hunter2", "pin 是 4821", "wifi 是 CoffeeShop2024".
        # The value is required: a bare "pin the message" or "the wifi is slow"
        # must not fire. Chinese separators (是/为) count — this user writes both.
        re.compile(
            r"\b(?:password|passwd|pwd|api[_ -]?key|access[_ -]?key|secret[_ -]?key"
            r"|token|secret|pin|otp|passphrase|wifi|wi-fi)\b\s*(?:is|are|:|=|是|为)\s*\S+",
            re.I,
        ),
        # Common secret prefixes / shapes.
        re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{20,})"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                       # AWS access key id
        re.compile(r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}", re.I),    # bearer token
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
}

# --- things that are merely *about* a sensitive topic ---------------------------
#
# These inform, they do not interrupt. Most of the professional wedge lives here.

_NOTICE_TERMS: Dict[str, Tuple[str, ...]] = {
    "financial": (
        "salary", "compensation", "equity", "bonus", "wire transfer", "invoice",
        "bank account", "routing number", "credit card",
    ),
    "medical": (
        "diagnosis", "prescription", "medication", "therapist", "surgery",
        "biopsy", "oncology",
    ),
    "legal": (
        "lawsuit", "attorney", "settlement", "subpoena", "contract dispute",
        "litigation",
    ),
    "work-confidential": (
        "confidential", "nda", "offer letter", "term sheet", "cap table",
        "layoff", "acquisition",
    ),
}

_NOTICE_CJK: Dict[str, Tuple[str, ...]] = {
    "financial": ("工资", "薪资", "薪水", "银行卡", "转账", "汇款", "股票账户", "报销"),
    "medical": ("诊断", "处方", "吃药", "医生说", "手术", "化验", "病历"),
    "legal": ("律师", "起诉", "诉讼", "合同纠纷", "仲裁", "法务"),
    "work-confidential": ("保密", "offer", "期权", "裁员", "收购"),
}


def _term_pattern(terms: Tuple[str, ...]) -> re.Pattern:
    # \b around each ASCII term: "password" must not fire inside "passwordless".
    joined = "|".join(re.escape(term) for term in terms)
    return re.compile(rf"\b(?:{joined})\b", re.I)


_BLOCK_TERM_RE = {name: _term_pattern(terms) for name, terms in _BLOCK_TERMS.items()}
_NOTICE_TERM_RE = {name: _term_pattern(terms) for name, terms in _NOTICE_TERMS.items()}


def _scan(text: str, term_res, cjk_terms, patterns=None) -> List[str]:
    found = []
    for category, regex in term_res.items():
        if regex.search(text):
            found.append(category)
    for category, terms in cjk_terms.items():
        if category not in found and any(term in text for term in terms):
            found.append(category)
    for category, regexes in (patterns or {}).items():
        if category not in found and any(regex.search(text) for regex in regexes):
            found.append(category)
    return found


def scan_blocking(text: str) -> List[str]:
    """Categories that must stop and ask before the text reaches a cloud model."""
    found = _scan(text, _BLOCK_TERM_RE, _BLOCK_CJK, _BLOCK_PATTERNS)
    if "financial-id" not in found and _has_card_number(text):
        found.append("financial-id")
    return found


def scan_notice(text: str) -> List[str]:
    """Sensitive *topics*. Worth telling the user about; not worth interrupting."""
    return _scan(text, _NOTICE_TERM_RE, _NOTICE_CJK)


def scan_sensitive(text: str) -> List[str]:
    """Every category found, blocking first. Use for display and reporting."""
    blocking = scan_blocking(text)
    return blocking + [c for c in scan_notice(text) if c not in blocking]


def describe_warning(categories: List[str]) -> str:
    """The hard gate's message: something secret is actually in the text."""
    joined = ", ".join(categories)
    return (
        f"Heads up: this conversation appears to contain {joined} content. "
        "The context will be sent to a cloud model to draft replies."
    )


def describe_notice(categories: List[str]) -> str:
    """The passive line: a sensitive topic, but nothing secret in the text."""
    joined = ", ".join(categories)
    return f"Note: this conversation touches on {joined}. Context goes to a cloud model."
