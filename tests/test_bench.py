"""Smoke tests for the synthetic bench.

Run with: pytest -q
"""

import json
from pathlib import Path

import pytest

from bench.generator import build_canonical_df
from bench.ground_truth import generate_ground_truth
from bench.templates import TEMPLATES
from bench.verifier import verify


def test_canonical_df_deterministic():
    """Same (spec, seed) must produce identical DataFrame across runs."""
    spec = TEMPLATES[0]
    df1 = build_canonical_df(spec, seed=42)
    df2 = build_canonical_df(spec, seed=42)
    assert df1.equals(df2)


def test_canonical_df_shapes():
    for spec in TEMPLATES:
        df = build_canonical_df(spec, seed=42)
        assert len(df) == spec.n_companies, f"{spec.file_id} row count mismatch"
        assert df["Fund"].nunique() == spec.n_funds, f"{spec.file_id} fund count mismatch"
        # Every row must have a non-null Entry EV and EBITDA
        assert df["Entry EV"].isna().sum() == 0
        assert df["Entry EBITDA"].isna().sum() == 0
        # Realized rows must have an Exit Date and Exit EV
        realized = df[df["Status"] == "Realized"]
        assert realized["Exit Date"].isna().sum() == 0
        assert realized["Exit EV"].isna().sum() == 0


def test_ground_truth_q1_matches_fund_count():
    """Q1 (How many funds?) ground truth must equal n_funds for each template."""
    for spec in TEMPLATES:
        df = build_canonical_df(spec, seed=42)
        records = generate_ground_truth(df, spec.file_id, "A", sample_size=3, seed=42)
        q1 = next(r for r in records if r["template_id"] == 1)
        assert q1["ground_truth"] == spec.n_funds


def test_ground_truth_q16_is_finite():
    """Q16 (median net debt/EBITDA) must be a finite positive number."""
    for spec in TEMPLATES:
        df = build_canonical_df(spec, seed=42)
        records = generate_ground_truth(df, spec.file_id, "A", sample_size=3, seed=42)
        q16 = next(r for r in records if r["template_id"] == 16)
        gt = q16["ground_truth"]
        assert isinstance(gt, (int, float))
        assert 0.1 < gt < 20  # plausible debt/EBITDA range


def test_verifier_numeric():
    v = verify("The answer is 2.4.", 2.4, "numeric")
    assert v.correct and v.tier == 1
    v = verify("Roughly 2.45.", 2.4, "numeric")
    assert v.correct and v.tier in (1, 2)
    v = verify("It is 8.", 2.4, "numeric")
    assert not v.correct


def test_verifier_numeric_large_values_regression():
    """Q10 baseline failures: response exactly matches GT but verifier
    was returning False because the thousands-separator regex alternative
    greedy-matched only 3 digits of '1418.4', leaving '8.4' as the
    extracted value. Regression covers the failure shapes observed in
    the 2.5 Pro baseline run.
    """
    # Bare large numbers, identical response and GT
    for value in [1418.4, 1402.0, 1461.1, 1385.4, 1350.5]:
        v = verify(str(value), value, "numeric")
        assert v.correct, f"large bare number {value} not recognized"
        assert v.tier == 1
    # Integer response for a float GT
    v = verify("1402", 1402.0, "numeric")
    assert v.correct
    # Comma-formatted large number (must still work)
    v = verify("1,418.4", 1418.4, "numeric")
    assert v.correct
    # Number embedded in narrative — last token still wins
    v = verify("The entry EV is 1418.4 million.", 1418.4, "numeric")
    assert v.correct


def test_verifier_string_in_narrative():
    v = verify("The latest fund is Fund IV.", "Fund IV", "string")
    assert v.correct


def test_verifier_list_extraction():
    expected = ["Apex", "Bloom", "Cipher"]
    v = verify("1. Apex\n2. Bloom\n3. Cipher", expected, "list")
    assert v.correct
    v = verify("Apex, Bloom, Cipher.", expected, "list")
    assert v.correct


def test_verifier_dict_exact_match():
    """The Failure 9 case — perfect dict response should score correct at Tier 1."""
    expected = {"Fund I": 6, "Fund II": 9, "Fund III": 9}
    response = "Fund I: 6\nFund II: 9\nFund III: 9"
    v = verify(response, expected, "dict")
    assert v.correct
    assert v.tier == 1


def test_verifier_dict_string_values():
    """Dict with string values (Q6: per-fund highest company)."""
    expected = {"Growth": "Junction GmbH", "Income": "Orion Pharma"}
    response = "Growth: Junction GmbH\nIncome: Orion Pharma"
    v = verify(response, expected, "dict")
    assert v.correct


def test_verifier_dict_partial_truncation_fails():
    """Truncated response (Failure 3) should not pass at Tier 1."""
    expected = {
        "Growth": "Junction GmbH",
        "Income": "Orion Pharma",
        "Stability": "Cobalt Pharma",
        "Diversify": "Orion Inc",
    }
    response = "Growth: Junction GmbH\nIncome: Orion Pharma\nStability: Cobalt Pharma\nDiversify:"
    v = verify(response, expected, "dict")
    # The "Diversify:" line has no value, so parse skips it; only 3/4 keys present
    assert not v.correct


def test_verifier_dict_tolerant_numbers():
    """Tier 2 accepts dict values within 5% tolerance."""
    expected = {"Fund I": 100.0, "Fund II": 200.0}
    response = "Fund I: 102.0\nFund II: 196.0"  # 2% and 2% off — within 2.5%
    v = verify(response, expected, "dict")
    assert v.correct
    # Now 4% off — fails Tier 1, passes Tier 2
    response2 = "Fund I: 104.0\nFund II: 208.0"
    v2 = verify(response2, expected, "dict")
    assert v2.correct
    assert v2.tier == 2


def test_verifier_dict_markdown_bold():
    """LLMs sometimes wrap keys in ** ... ** markdown bold."""
    expected = {"Fund I": 47.3, "Fund II": 89.1}
    response = "**Fund I**: 47.3\n**Fund II**: 89.1"
    v = verify(response, expected, "dict")
    assert v.correct


def test_verifier_dict_with_currency():
    """LLMs sometimes include currency symbols / units in the value."""
    expected = {"Fund I": 47.3, "Fund II": 89.1}
    response = "Fund I: $47.3M\nFund II: $89.1 million"
    v = verify(response, expected, "dict")
    assert v.correct


def test_bench_artifacts_exist():
    """The build script should have produced the expected artifacts."""
    base = Path("bench/data")
    if not base.exists():
        pytest.skip("Bench not built yet — run `python -m bench.build` first")
    assert (base / "ground_truth.jsonl").exists()
    assert (base / "manifest.json").exists()
    manifest = json.loads((base / "manifest.json").read_text())
    # n_files = (number of base templates) * 3 versions
    expected_files = len(TEMPLATES) * 3
    assert manifest["n_files"] == expected_files, (
        f"Expected {expected_files} files for {len(TEMPLATES)} templates, got {manifest['n_files']}"
    )
    # Roughly 22 questions per file
    assert manifest["n_questions_total"] >= expected_files * 18
