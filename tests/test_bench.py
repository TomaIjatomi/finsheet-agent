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


def test_verifier_string_in_narrative():
    v = verify("The latest fund is Fund IV.", "Fund IV", "string")
    assert v.correct


def test_verifier_list_extraction():
    expected = ["Apex", "Bloom", "Cipher"]
    v = verify("1. Apex\n2. Bloom\n3. Cipher", expected, "list")
    assert v.correct
    v = verify("Apex, Bloom, Cipher.", expected, "list")
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
        f"Expected {expected_files} files for {len(TEMPLATES)} templates, "
        f"got {manifest['n_files']}"
    )
    # Roughly 22 questions per file
    assert manifest["n_questions_total"] >= expected_files * 18
