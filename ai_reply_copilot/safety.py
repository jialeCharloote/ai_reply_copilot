"""Sensitive-content detection (PRD P0).

Before conversation context is sent to a cloud LLM, scan it for signs of
personal, financial, medical, legal, or work-confidential information and let
the user decide whether to continue. This is a lightweight bilingual keyword
heuristic, not a classifier — it errs toward a gentle reminder, never a block.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# Category -> bilingual keyword list. Matching is case-insensitive for ASCII.
_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "financial": (
        "bank account", "routing number", "credit card", "ssn",
        "social security", "salary", "wire transfer", "iban",
        "账号", "银行卡", "工资", "薪资", "转账", "汇款", "股票账户",
    ),
    "medical": (
        "diagnosis", "prescription", "medication", "therapist", "surgery",
        "诊断", "处方", "吃药", "医生说", "手术", "化验", "病历",
    ),
    "legal": (
        "lawsuit", "attorney", "settlement", "subpoena", "contract dispute",
        "律师", "起诉", "诉讼", "合同纠纷", "仲裁", "法务",
    ),
    "work-confidential": (
        "confidential", "nda", "do not share", "internal only", "trade secret",
        "offer letter", "机密", "保密", "内部资料", "别外传", "不要外传",
    ),
    "credentials": (
        "password", "api key", "access token", "private key", "passcode",
        "密码", "验证码", "秘钥", "私钥",
    ),
}


def scan_sensitive(text: str) -> List[str]:
    """Return the list of sensitive categories that appear in ``text``."""
    lowered = text.lower()
    found = []
    for category, keywords in _CATEGORIES.items():
        if any(keyword in lowered for keyword in keywords):
            found.append(category)
    return found


def describe_warning(categories: List[str]) -> str:
    joined = ", ".join(categories)
    return (
        f"Heads up: this conversation appears to contain {joined} content. "
        "The context will be sent to a cloud model to draft replies."
    )
