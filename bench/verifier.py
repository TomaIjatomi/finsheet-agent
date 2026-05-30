"""
3-tier cascading verifier for free-text LLM answers.

Following FinSheet-Bench Section 4.3.1:
  Tier 1: exact match (regex, case-insensitive string, Jaccard 0.95,
          numeric 2.5% relative tolerance, dates 1-day tolerance, boolean keywords)
  Tier 2: fuzzy match (broader regex, sequence similarity 0.95, Jaccard 0.75,
          numeric 5% relative tolerance)
  Tier 3: LLM adjudication (left as a hook: implement when the agent stack is up;
          can use Gemini 3 Flash as the judge)

Returns a Verdict object with the tier that resolved it, the confidence score,
and the boolean correctness.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

import dateutil.parser as dp


@dataclass
class Verdict:
    correct: bool
    tier: int                      # 1, 2, or 3
    confidence: float              # 0..1
    extracted_value: Any
    explanation: str


# -------- Tier 1: exact ----------------------------------------------------

NUMERIC_REGEX = re.compile(r"(?<![A-Za-z])(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)(?![A-Za-z])")
NUMERIC_REGEX_LOOSE = re.compile(r"(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)")


def _extract_number_strict(s: str) -> float | None:
    matches = NUMERIC_REGEX.findall(s)
    if not matches:
        return None
    try:
        # Prefer the last numeric token (often the final answer in LLM output)
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def _extract_number_loose(s: str) -> float | None:
    matches = NUMERIC_REGEX_LOOSE.findall(s)
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def _within_tolerance(actual: float, expected: float, tol: float) -> bool:
    if expected == 0:
        return abs(actual) < tol
    return abs(actual - expected) / abs(expected) <= tol


def _norm_str(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def _extract_items(s: str) -> list[str]:
    """Heuristic: split on commas, semicolons, newlines; strip numbering and
    surrounding punctuation. Lowercased for case-insensitive comparison.
    """
    # Strip leading "1. " or "1) " numbering
    cleaned = re.sub(r"\b\d+[\.\)]\s+", "", s)
    parts = re.split(r"[,;\n]", cleaned)
    out = []
    for p in parts:
        p = p.strip()
        # Strip trailing/leading punctuation (commas, periods, semicolons, quotes, brackets)
        p = p.strip(".,;:'\"()[]{}")
        # Strip leading articles
        p = re.sub(r"^(the|a|an)\s+", "", p, flags=re.IGNORECASE)
        if p:
            out.append(p.lower())
    return out


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def _parse_date(s: str) -> date | None:
    s = s.strip()
    try:
        d = dp.parse(s, fuzzy=True, default=datetime(2000, 1, 1))
        return d.date()
    except (ValueError, TypeError, OverflowError):
        return None


def _bool_match(s: str, expected: str) -> bool:
    s_low = s.lower()
    if expected.lower() == "realized":
        return "realized" in s_low and "unrealized" not in s_low.replace("realized", "", 1)
    if expected.lower() == "unrealized":
        return "unrealized" in s_low
    if expected.lower() in ("yes", "true"):
        return any(t in s_low for t in ("yes", "true"))
    if expected.lower() in ("no", "false"):
        return any(t in s_low for t in ("no", "false"))
    return expected.lower() in s_low


# -------- Tier 1 verify ----------------------------------------------------

def verify_tier1(answer_text: str, expected: Any, answer_type: str) -> Verdict | None:
    """Strict rule-based extraction. Returns a verdict only when the answer
    can be resolved at confidence >= 0.95 — either matching (correct=True)
    or unambiguously wrong (clean extraction far outside both strict and
    Tier-2 tolerances). Ambiguous cases defer to Tier 2.
    """
    if answer_type == "numeric":
        n = _extract_number_strict(answer_text)
        if n is None:
            return None
        if _within_tolerance(n, float(expected), 0.025):
            return Verdict(True, 1, 0.98, n, "Tier 1 numeric exact (2.5% tol)")
        # Defer to Tier 2 if within 5% (Tier 2's threshold) — Tier 2 can still
        # accept it. Only return negative at Tier 1 if it's clearly wrong by a
        # wide margin (more than 2x Tier 2's threshold).
        if _within_tolerance(n, float(expected), 0.10):
            return None  # ambiguous, defer
        return Verdict(False, 1, 0.95, n, "Tier 1 numeric clearly wrong (>10% off)")

    if answer_type == "string":
        if _norm_str(answer_text) == _norm_str(str(expected)):
            return Verdict(True, 1, 1.0, answer_text.strip(), "Tier 1 string exact")
        if _norm_str(str(expected)) in _norm_str(answer_text):
            return Verdict(True, 1, 0.96, str(expected), "Tier 1 expected contained in answer")
        return None  # Defer

    if answer_type == "bool":
        if _bool_match(answer_text, str(expected)):
            return Verdict(True, 1, 0.97, str(expected), "Tier 1 boolean keyword match")
        return None  # Defer, in case answer is phrased oddly

    if answer_type == "date":
        d = _parse_date(answer_text)
        if d is None:
            return None
        try:
            exp_d = _parse_date(str(expected))
            if exp_d is None:
                return None
            if abs((d - exp_d).days) <= 1:
                return Verdict(True, 1, 0.98, d.isoformat(), "Tier 1 date within 1 day")
            # Date clearly wrong if more than 30 days off
            if abs((d - exp_d).days) > 30:
                return Verdict(False, 1, 0.95, d.isoformat(), "Tier 1 date clearly wrong")
            return None  # Defer for borderline mismatch
        except Exception:
            return None

    if answer_type == "list":
        if not isinstance(expected, list):
            return None
        items = set(_extract_items(answer_text))
        exp = set(s.lower() for s in expected)
        score = _jaccard(items, exp)
        if score >= 0.95:
            return Verdict(True, 1, 0.97, sorted(items), f"Tier 1 Jaccard {score:.2f}")
        return None  # Defer

    if answer_type == "dict":
        return None  # Defer to Tier 3 LLM

    return None


# -------- Tier 2: fuzzy ----------------------------------------------------

def verify_tier2(answer_text: str, expected: Any, answer_type: str) -> Verdict | None:
    """Tolerant rule-based pass. Returns Verdict if confidence >= 0.70."""
    if answer_type == "numeric":
        n = _extract_number_loose(answer_text)
        if n is None:
            return None
        if _within_tolerance(n, float(expected), 0.05):
            return Verdict(True, 2, 0.80, n, "Tier 2 numeric (5% tol)")
        return Verdict(False, 2, 0.80, n, "Tier 2 numeric out of tolerance")

    if answer_type == "string":
        ratio = SequenceMatcher(None, _norm_str(answer_text), _norm_str(str(expected))).ratio()
        if ratio >= 0.95:
            return Verdict(True, 2, 0.78, answer_text.strip(), f"Tier 2 seq sim {ratio:.2f}")
        # Substring fallback
        if _norm_str(str(expected)) in _norm_str(answer_text):
            return Verdict(True, 2, 0.75, str(expected), "Tier 2 substring match")
        return None

    if answer_type == "list":
        if not isinstance(expected, list):
            return None
        items = set(_extract_items(answer_text))
        exp = set(s.lower() for s in expected)
        score = _jaccard(items, exp)
        if score >= 0.75:
            return Verdict(True, 2, 0.78, sorted(items),
                           f"Tier 2 Jaccard {score:.2f}")
        return Verdict(False, 2, 0.75, sorted(items),
                       f"Tier 2 Jaccard {score:.2f} below threshold")

    return None


# -------- Tier 3: LLM adjudication hook -----------------------------------

LLMJudge = Callable[[str, Any, str], Verdict]
"""Signature: (answer_text, expected, answer_type) -> Verdict.
Implement using Gemini 3 Flash or similar once the agent stack is up.
"""

def _default_llm_judge_hook(answer_text: str, expected: Any, answer_type: str) -> Verdict:
    """Placeholder until the LLM judge is wired up. Marks unresolved."""
    return Verdict(
        correct=False,
        tier=3,
        confidence=0.0,
        extracted_value=None,
        explanation="LLM judge not configured. Set verify(llm_judge=...) to enable Tier 3.",
    )


# -------- Public entry point ----------------------------------------------

def verify(answer_text: str, expected: Any, answer_type: str,
           llm_judge: LLMJudge | None = None) -> Verdict:
    """Cascading verification. Returns final Verdict.

    Tier 1 -> Tier 2 -> Tier 3 (LLM adjudication if provided).
    """
    v1 = verify_tier1(answer_text, expected, answer_type)
    if v1 is not None and v1.confidence >= 0.95:
        return v1

    v2 = verify_tier2(answer_text, expected, answer_type)
    if v2 is not None and v2.confidence >= 0.70:
        return v2

    judge = llm_judge or _default_llm_judge_hook
    return judge(answer_text, expected, answer_type)
