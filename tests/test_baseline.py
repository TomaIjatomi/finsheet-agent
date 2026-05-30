"""Tests for the M1.3 baseline harness.

Uses a mocked Gemini client so tests run without GCP access. Validates:
  - Spreadsheet serializer output structure
  - Solver protocol + retry / error handling
  - Runner resumability + concurrency
  - Aggregate scoring
  - Report rendering
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from finsheet.baselines.prompts import FORMAT_HINTS, build_user_prompt
from finsheet.baselines.report import aggregate, render_report
from finsheet.baselines.runner import run_baseline
from finsheet.baselines.serializer import estimate_tokens, serialize_xlsx
from finsheet.baselines.solver import FullContextSolver

# ---- Mock Gemini client -------------------------------------------------


class MockClient:
    """Mimics google.genai.Client just enough for testing."""

    def __init__(self, response_factory):
        self._factory = response_factory
        self.calls = 0
        self.aio = SimpleNamespace(models=self)

    async def generate_content(self, model, contents, config):  # noqa: ARG002
        self.calls += 1
        text, tokens_in, tokens_out = self._factory(self.calls, contents)
        return SimpleNamespace(
            text=text,
            usage_metadata=SimpleNamespace(
                prompt_token_count=tokens_in,
                candidates_token_count=tokens_out,
            ),
        )


# ---- Serializer ---------------------------------------------------------


def test_serializer_produces_pipe_separated_rows():
    bench_files = Path("bench/data/files")
    if not bench_files.exists():
        pytest.skip("Bench not built")
    text = serialize_xlsx(bench_files / "synthetic1_A.xlsx")
    assert text.startswith("| ")
    assert " | " in text
    # Should have many rows
    lines = text.split("\n")
    assert len(lines) > 40


def test_serializer_preserves_fund_dividers():
    bench_files = Path("bench/data/files")
    if not bench_files.exists():
        pytest.skip("Bench not built")
    # synthetic3_A uses fund-as-row-separator
    text = serialize_xlsx(bench_files / "synthetic3_A.xlsx")
    # Fund I should appear as a row by itself somewhere
    assert "Fund I" in text


def test_serializer_handles_multiline_headers():
    bench_files = Path("bench/data/files")
    if not bench_files.exists():
        pytest.skip("Bench not built")
    text = serialize_xlsx(bench_files / "synthetic4_A.xlsx")
    # Multi-line headers have embedded newlines in cells; check serialization
    # contains the header tokens
    assert "Entry" in text
    assert "EBITDA" in text


def test_estimate_tokens_roughly_correct():
    assert estimate_tokens("hello world") < 10
    assert estimate_tokens("x" * 400) >= 80


# ---- Prompts ------------------------------------------------------------


def test_prompts_include_format_hints():
    for atype, hint in FORMAT_HINTS.items():
        p = build_user_prompt("table data", "what?", atype)
        assert hint in p
        assert "table data" in p
        assert "what?" in p


# ---- Solver -------------------------------------------------------------


def test_solver_normal_call(tmp_path):
    bench_files = Path("bench/data/files")
    if not bench_files.exists():
        pytest.skip("Bench not built")

    def factory(call_n, _contents):
        return "4", 1234, 5

    client = MockClient(factory)
    solver = FullContextSolver(client, model="mock")
    result = asyncio.run(
        solver.solve(bench_files / "synthetic1_A.xlsx", "How many funds?", "numeric")
    )
    assert result.error is None
    assert result.answer_text == "4"
    assert result.tokens_in == 1234
    assert result.tokens_out == 5
    assert result.cost_usd > 0


def test_solver_caches_serialization(tmp_path):
    bench_files = Path("bench/data/files")
    if not bench_files.exists():
        pytest.skip("Bench not built")

    def factory(_call_n, _contents):
        return "x", 100, 1

    client = MockClient(factory)
    solver = FullContextSolver(client, model="mock")
    xlsx = bench_files / "synthetic1_A.xlsx"
    # Call twice; second should be cached
    asyncio.run(solver.solve(xlsx, "q1", "string"))
    asyncio.run(solver.solve(xlsx, "q2", "string"))
    assert len(solver._serialization_cache) == 1


def test_solver_captures_error():
    def factory(_call_n, _contents):
        raise RuntimeError("simulated API error")

    client = MockClient(factory)
    solver = FullContextSolver(client, model="mock")
    bench_files = Path("bench/data/files")
    if not bench_files.exists():
        pytest.skip("Bench not built")
    result = asyncio.run(solver.solve(bench_files / "synthetic1_A.xlsx", "q", "numeric"))
    assert result.error is not None
    assert "RuntimeError" in result.error


# ---- Runner -------------------------------------------------------------


def test_runner_end_to_end(tmp_path):
    """Run the full runner against a tiny subset with a perfect-answer mock."""
    bench_dir = Path("bench/data")
    if not (bench_dir / "ground_truth.jsonl").exists():
        pytest.skip("Bench not built")

    # Load 3 questions, build a mock that returns each one's ground truth as a string
    with open(bench_dir / "ground_truth.jsonl") as f:
        records = [json.loads(line) for line in f][:3]

    answer_iter = iter([str(r["ground_truth"]) for r in records])

    def factory(_call_n, _contents):
        try:
            return next(answer_iter), 100, 5
        except StopIteration:
            return "0", 100, 5

    client = MockClient(factory)
    solver = FullContextSolver(client, model="mock")

    results_path = tmp_path / "results.jsonl"
    summary = asyncio.run(
        run_baseline(
            solver=solver,
            bench_data_dir=bench_dir,
            results_path=results_path,
            concurrency=2,
            limit=3,
        )
    )
    assert summary["total"] == 3
    assert results_path.exists()
    # Verify written records have the expected structure
    lines = results_path.read_text().strip().split("\n")
    assert len(lines) == 3
    d = json.loads(lines[0])
    assert "verdict_correct" in d
    assert "tokens_in" in d
    assert "solver" in d


def test_runner_resumes(tmp_path):
    """If results exist for some qids, a re-run should skip them."""
    bench_dir = Path("bench/data")
    if not (bench_dir / "ground_truth.jsonl").exists():
        pytest.skip("Bench not built")

    results_path = tmp_path / "results.jsonl"

    # First pass: 2 questions
    def factory(_call_n, _contents):
        return "42", 100, 5

    client1 = MockClient(factory)
    asyncio.run(
        run_baseline(
            solver=FullContextSolver(client1, model="mock"),
            bench_data_dir=bench_dir,
            results_path=results_path,
            concurrency=1,
            limit=2,
        )
    )
    first_count = len(results_path.read_text().strip().split("\n"))

    # Second pass: limit 2 (should skip already-done, do 2 new)
    client2 = MockClient(factory)
    asyncio.run(
        run_baseline(
            solver=FullContextSolver(client2, model="mock"),
            bench_data_dir=bench_dir,
            results_path=results_path,
            concurrency=1,
            limit=2,
        )
    )
    second_count = len(results_path.read_text().strip().split("\n"))
    # Client 2 makes 2 NEW calls (the prior 2 are skipped)
    assert client2.calls == 2
    assert second_count > first_count


# ---- Scoring + Report ---------------------------------------------------


def test_aggregate_computes_overall(tmp_path):
    results = [
        {
            "qid": 1,
            "template_id": 1,
            "file_id": "synthetic1",
            "version": "A",
            "question": "q1",
            "category": "Simple Lookup",
            "complexity": "Low",
            "answer_type": "numeric",
            "ground_truth": 4,
            "raw_response": "4",
            "verdict_correct": True,
            "verdict_tier": 1,
            "verdict_confidence": 0.98,
            "verdict_explanation": "ok",
            "solver": "test",
            "tokens_in": 100,
            "tokens_out": 5,
            "latency_ms": 1500,
            "cost_usd": 0.001,
            "error": None,
            "timestamp": 0,
        },
        {
            "qid": 2,
            "template_id": 2,
            "file_id": "synthetic1",
            "version": "A",
            "question": "q2",
            "category": "Counting",
            "complexity": "Medium",
            "answer_type": "numeric",
            "ground_truth": 10,
            "raw_response": "8",
            "verdict_correct": False,
            "verdict_tier": 1,
            "verdict_confidence": 0.95,
            "verdict_explanation": "wrong",
            "solver": "test",
            "tokens_in": 100,
            "tokens_out": 5,
            "latency_ms": 1500,
            "cost_usd": 0.001,
            "error": None,
            "timestamp": 0,
        },
    ]
    agg = aggregate(results)
    assert agg["overall"]["accuracy"] == 0.5
    assert agg["by_category"]["Simple Lookup"]["accuracy"] == 1.0
    assert agg["by_category"]["Counting"]["accuracy"] == 0.0


def test_report_renders():
    results = [
        {
            "qid": 1,
            "template_id": 1,
            "file_id": "synthetic4",
            "version": "A",
            "question": "How many funds?",
            "category": "Simple Lookup",
            "complexity": "Low",
            "answer_type": "numeric",
            "ground_truth": 8,
            "raw_response": "8",
            "verdict_correct": True,
            "verdict_tier": 1,
            "verdict_confidence": 0.98,
            "verdict_explanation": "ok",
            "solver": "test",
            "tokens_in": 8000,
            "tokens_out": 5,
            "latency_ms": 3000,
            "cost_usd": 0.015,
            "error": None,
            "timestamp": 0,
        },
    ]
    agg = aggregate(results)
    md = render_report(results, agg, solver_name="test", wall_clock_minutes=0.5)
    assert "# Eval Report" in md
    assert "Headline" in md
    assert "synthetic4_A" in md
    assert "Cost" in md
