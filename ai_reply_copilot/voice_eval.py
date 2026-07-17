"""Style-fidelity eval: do the generated drafts actually sound like the user?

``charla voice learn`` distils the user's sent messages into a
:class:`~ai_reply_copilot.voice.VoiceProfile` — median length, emoji rate,
punctuation, capitalisation, how much they code-switch. That profile rides in
every prompt, and then nothing checks whether the drafts that come back honour
it. The only signal today is the user filing ``feedback not_like_me``: an
anecdote, after the fact, one draft at a time.

This module closes the loop deterministically. A set of drafts is run through
the *same* ``analyze_voice`` that learned the profile — the identical counters,
not a re-implementation — and each dimension is compared against the learned
value. The output is attribution, not a vibe: "emoji on 0.80 of drafts vs your
0.00" and "drafts ≈4× longer than you write", never just "doesn't sound like
you". No model call, no network; the same input always produces the same
report, which is what makes it usable as a regression harness — change the
prompt, rerun, see which dimension moved.

Two honest limits, stated up front rather than discovered later:

- **Statistics are the reliable half, not the whole.** A draft can match your
  length, emoji rate and punctuation and still not *sound* like you — word
  choice and rhythm live in the exemplars and the model, not in these
  counters. A red dimension is measurably wrong; all-green means
  "statistically consistent", not "indistinguishable from you".
- **Rates are coarse on small draft sets.** Every rate is a fraction of the
  set, so with five drafts one message moves it by 0.2. The tolerances below
  are sized for that; give the eval at least five drafts per set.

The bundled fixture is synthetic — invented people, invented drafts. This repo
is public, so real chat logs must never appear in it, not even "anonymised".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .voice import VoiceProfile, analyze_voice

# A rate is a fraction of the draft set: with five drafts a single message
# moves it by 0.2, so anything tighter than this flags sampling noise as style
# drift.
RATE_TOLERANCE = 0.25

# Length is a scale, not a fraction. Being 30% longer than a 24-char texter is
# invisible; being twice as long reads as a different person. The absolute
# slack keeps very short profiles (median 10 chars) from flagging a 25-char
# draft that any human would call the same register.
LENGTH_RATIO = 1.8
LENGTH_SLACK_CHARS = 20

# Every dimension the eval scores, in report order. Each attr is a numeric
# field on VoiceProfile, so both sides of the comparison come out of the same
# analyze_voice counters and the eval cannot drift from the learner.
DIMENSIONS = [
    ("median_chars", "length"),
    ("emoji_rate", "emoji"),
    ("ends_with_period_rate", "final period"),
    ("starts_lowercase_rate", "lowercase start"),
    ("exclamation_rate", "exclamation marks"),
    ("question_rate", "questions asked"),
    ("chinese_rate", "language mix (share of Chinese)"),
]

DIMENSION_NAMES = [attr for attr, _ in DIMENSIONS]

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "voice_eval.json"
)


@dataclass
class DimensionCheck:
    dimension: str  # VoiceProfile field name
    label: str
    target: float   # what the user's own messages measure
    observed: float  # what the drafts measure
    off: bool
    detail: str     # the human line: numbers and direction, no adjectives


@dataclass
class DraftReport:
    """One draft set scored against one profile, dimension by dimension."""

    drafts: int
    checks: List[DimensionCheck]

    @property
    def off_dimensions(self) -> List[str]:
        return [c.dimension for c in self.checks if c.off]

    @property
    def faithful(self) -> bool:
        return not self.off_dimensions


def _check_length(label: str, target: float, observed: float) -> DimensionCheck:
    low, high = sorted((target, observed))
    times = high / max(low, 1.0)
    off = abs(observed - target) > LENGTH_SLACK_CHARS and times > LENGTH_RATIO
    detail = f"median {int(observed)} chars vs your {int(target)}"
    if off:
        direction = "longer" if observed > target else "shorter"
        detail += f" — ≈{times:.1f}× {direction}"
    return DimensionCheck(
        dimension="median_chars", label=label, target=target, observed=observed,
        off=off, detail=detail,
    )


def _check_rate(attr: str, label: str, target: float, observed: float) -> DimensionCheck:
    off = abs(observed - target) > RATE_TOLERANCE
    detail = f"{observed:.2f} of drafts vs your {target:.2f}"
    if off:
        if attr == "chinese_rate":
            detail += (
                " — more Chinese than you actually write"
                if observed > target
                else " — the drafts dropped your Chinese"
            )
        elif observed > target:
            # Near zero a ratio explodes into nonsense ("40× your rate" when
            # the user did it once), so below 0.05 say what it means instead.
            detail += (
                f" — ≈{observed / target:.1f}× your rate"
                if target >= 0.05
                else " — a habit you don't have"
            )
        else:
            detail += " — a habit of yours the drafts dropped"
    return DimensionCheck(
        dimension=attr, label=label, target=target, observed=observed,
        off=off, detail=detail,
    )


def compare_drafts(profile: VoiceProfile, drafts) -> DraftReport:
    """Score a set of drafts against a learned profile.

    The drafts go through the exact ``analyze_voice`` that produced the
    profile, so a disagreement is always a real difference in the text, never
    a difference between two implementations of the same statistic.
    """
    if not profile or not profile.sampled:
        raise ValueError("no voice profile to compare against — nothing was learned")
    observed = analyze_voice(drafts)
    if not observed.sampled:
        raise ValueError("no drafts to score")

    checks: List[DimensionCheck] = []
    for attr, label in DIMENSIONS:
        target = float(getattr(profile, attr))
        got = float(getattr(observed, attr))
        if attr == "median_chars":
            checks.append(_check_length(label, target, got))
        else:
            checks.append(_check_rate(attr, label, target, got))
    return DraftReport(drafts=observed.sampled, checks=checks)


# ── The labelled fixture ──────────────────────────────────────────────────────
# Shape: {"voice_samples": [...], "draft_sets": [{"name", "why", "drafts",
# "expect_off"}]}. `expect_off` lists the dimensions each set deliberately
# deviates on, which turns the fixture into a self-checking eval: the run
# fails when the detector misses a labelled deviation OR flags beyond the
# labels — either direction means the eval (or analyze_voice) regressed.


def load_eval_set(path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(path) if path else DEFAULT_FIXTURE
    try:
        eval_set = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"eval fixture not found: {path}")
    if not eval_set.get("voice_samples") or not eval_set.get("draft_sets"):
        raise ValueError(f"{path} needs 'voice_samples' and 'draft_sets'")
    return eval_set


@dataclass
class SetOutcome:
    name: str
    why: str
    report: DraftReport
    expected_off: List[str]

    @property
    def missed(self) -> List[str]:
        """Labelled deviations the eval failed to flag."""
        return [d for d in self.expected_off if d not in self.report.off_dimensions]

    @property
    def unexpected(self) -> List[str]:
        """Flags beyond the fixture's labels."""
        return [d for d in self.report.off_dimensions if d not in self.expected_off]

    @property
    def agrees(self) -> bool:
        return not self.missed and not self.unexpected


@dataclass
class EvalReport:
    profile: VoiceProfile
    outcomes: List[SetOutcome]

    @property
    def agrees(self) -> bool:
        return all(outcome.agrees for outcome in self.outcomes)


def evaluate(eval_set: Dict[str, Any]) -> EvalReport:
    """Learn a profile from the fixture's synthetic messages, score every
    draft set against it, and check the flags match the fixture's labels."""
    profile = analyze_voice(eval_set["voice_samples"])
    outcomes = []
    for case in eval_set["draft_sets"]:
        report = compare_drafts(profile, case["drafts"])
        outcomes.append(
            SetOutcome(
                name=case["name"],
                why=case.get("why", ""),
                report=report,
                expected_off=list(case.get("expect_off", [])),
            )
        )
    return EvalReport(profile=profile, outcomes=outcomes)


def format_report(result: EvalReport) -> str:
    lines: List[str] = []
    lines.append(
        f"Voice fidelity — {len(result.outcomes)} draft sets vs a profile "
        f"learned from {result.profile.sampled} messages"
    )
    lines.append("=" * 64)
    for outcome in result.outcomes:
        report = outcome.report
        verdict = (
            "matches the voice"
            if report.faithful
            else f"off on {len(report.off_dimensions)} of {len(report.checks)} dimensions"
        )
        lines.append(f"\n{outcome.name} ({report.drafts} drafts) — {verdict}")
        if outcome.why:
            lines.append(f"  ({outcome.why})")
        for check in report.checks:
            mark = "✗" if check.off else "✓"
            lines.append(f"  {mark} {check.label}: {check.detail}")
        if outcome.missed:
            lines.append(
                f"  !! labelled deviation NOT flagged: {', '.join(outcome.missed)}"
            )
        if outcome.unexpected:
            lines.append(
                f"  !! flagged beyond the labels: {', '.join(outcome.unexpected)}"
            )

    lines.append("\n" + "-" * 64)
    lines.append(
        "eval vs labels: agree — every labelled deviation was caught, nothing else flagged"
        if result.agrees
        else "eval vs labels: DISAGREE — the eval itself (or analyze_voice) changed behaviour"
    )
    return "\n".join(lines)
