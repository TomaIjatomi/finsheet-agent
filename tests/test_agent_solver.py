"""Tests for AgentSolver — the adapter that plugs the agent stack into
the baseline runner's Solver protocol.

Uses mocked Gemini (no GCP) + LocalSandbox (no Docker) + real bench files.
The point is to verify the wiring is correct, not to exercise the LLM.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from finsheet.agents.agent_solver import AgentSolver, _format_answer_text
from finsheet.mcp.sandbox import LocalSandbox

BENCH_FILES = Path("bench/data/files")


def _need_bench():
    if not BENCH_FILES.exists():
        pytest.skip("Bench not built")


# ---- _format_answer_text -----------------------------------------------


def test_format_answer_text_numeric_passthrough():
    assert _format_answer_text(42.5, "numeric") == "42.5"


def test_format_answer_text_numeric_int_floats():
    assert _format_answer_text(45.0, "numeric") == "45"


def test_format_answer_text_string_passthrough():
    assert _format_answer_text("Apex Holdings", "string") == "Apex Holdings"


def test_format_answer_text_list_joins():
    out = _format_answer_text(["Apex", "Bloom", "Cipher"], "list")
    assert out == "Apex, Bloom, Cipher"


def test_format_answer_text_dict_renders_kv_lines():
    out = _format_answer_text({"Fund I": 100, "Fund II": 200}, "dict")
    assert "Fund I: 100" in out
    assert "Fund II: 200" in out


def test_format_answer_text_bool_yes_no():
    assert _format_answer_text(True, "bool") == "yes"
    assert _format_answer_text(False, "bool") == "no"


def test_format_answer_text_none_is_empty():
    assert _format_answer_text(None, "numeric") == ""
    assert _format_answer_text(None, "string") == ""


# ---- AgentSolver end-to-end with mocks ---------------------------------


class _MockClient:
    """Returns canned responses keyed by call ordinal."""

    def __init__(self, response_factory):
        self._factory = response_factory
        self.calls: list[dict] = []
        self.aio = SimpleNamespace(models=self)

    async def generate_content(self, model, contents, config):  # noqa: ARG002
        self.calls.append({"contents": contents})
        text = self._factory(len(self.calls), contents)
        return SimpleNamespace(
            text=text,
            usage_metadata=SimpleNamespace(
                prompt_token_count=1500,
                candidates_token_count=150,
            ),
        )


def _canned_factory():
    """Decomposition returns a one-step QueryPlan; Computation returns a one-line code."""

    def factory(call_n: int, contents) -> str:
        if call_n == 1:
            # Decomposition: produce a QueryPlan JSON
            return json.dumps(
                {
                    "interpretation": "Count companies.",
                    "needed_columns": ["Company"],
                    "expected_answer_type": "numeric",
                    "expected_output_shape": "An integer.",
                    "subgoals": [
                        {
                            "step_number": 1,
                            "description": "Count rows",
                            "operation": "count",
                            "pandas_hint": "len(df)",
                        }
                    ],
                    "notes": "",
                }
            )
        # Computation: produce pandas code
        return "__result__ = len(df)"

    return factory


def test_agent_solver_end_to_end_numeric():
    """Full pipeline on a real bench file via mocked Gemini + LocalSandbox."""
    _need_bench()
    client = _MockClient(_canned_factory())
    sandbox = LocalSandbox(allow_unsafe=True)
    solver = AgentSolver(client=client, sandbox=sandbox)

    result = asyncio.run(
        solver.solve(
            BENCH_FILES / "synthetic1_A.xlsx",
            "How many companies are there?",
            "numeric",
        )
    )
    assert result.error is None
    # synthetic1_A has 45 companies (after prelude removes any non-company rows)
    assert result.answer_text == "45"
    # Decomposition (1500 in / 150 out) + Computation (1500 in / 150 out)
    assert result.tokens_in == 3000
    assert result.tokens_out == 300
    assert result.cost_usd > 0


def test_agent_solver_schema_card_is_cached():
    """Second question on the same file must not re-introspect the schema —
    important because the bench asks ~22 questions per file."""
    _need_bench()
    client = _MockClient(_canned_factory())
    sandbox = LocalSandbox(allow_unsafe=True)
    solver = AgentSolver(client=client, sandbox=sandbox)

    asyncio.run(
        solver.solve(
            BENCH_FILES / "synthetic1_A.xlsx",
            "Q1",
            "numeric",
        )
    )
    # Reset mock and ask again
    initial_call_count = len(client.calls)
    asyncio.run(
        solver.solve(
            BENCH_FILES / "synthetic1_A.xlsx",
            "Q2",
            "numeric",
        )
    )
    # Schema lives in solver._schema_cache; verify single cached SchemaCard
    assert len(solver._schema_cache) == 1
    # The Gemini call count went up (because decomp + comp still ran)
    assert len(client.calls) > initial_call_count


def test_agent_solver_propagates_decomposition_failure():
    """Bad JSON from decomposition LLM → SolveResult.error populated, empty answer."""
    _need_bench()

    def factory(call_n, contents):
        # First call (decomposition) returns invalid JSON
        return "this is not json"

    client = _MockClient(factory)
    sandbox = LocalSandbox(allow_unsafe=True)
    solver = AgentSolver(client=client, sandbox=sandbox)

    result = asyncio.run(
        solver.solve(
            BENCH_FILES / "synthetic1_A.xlsx",
            "Q",
            "numeric",
        )
    )
    assert result.error is not None
    assert "decomposition" in result.error.lower()
    assert result.answer_text == ""


def test_agent_solver_handles_computation_failure_gracefully():
    """If all retries fail, the solver returns empty answer + error,
    NOT a crash."""
    _need_bench()

    def factory(call_n, contents):
        if call_n == 1:
            return json.dumps(
                {
                    "interpretation": "x",
                    "needed_columns": ["Company"],
                    "expected_answer_type": "numeric",
                    "expected_output_shape": "x",
                    "subgoals": [
                        {
                            "step_number": 1,
                            "description": "x",
                            "operation": "count",
                            "pandas_hint": None,
                        }
                    ],
                    "notes": "",
                }
            )
        # All computation attempts emit broken code
        return "__result__ = df['NonexistentColumn'].sum()"

    client = _MockClient(factory)
    sandbox = LocalSandbox(allow_unsafe=True)
    solver = AgentSolver(client=client, sandbox=sandbox, max_retries=1)

    result = asyncio.run(
        solver.solve(
            BENCH_FILES / "synthetic1_A.xlsx",
            "Q",
            "numeric",
        )
    )
    assert result.error is not None
    assert result.answer_text == ""


def test_agent_solver_emits_dict_in_verifier_format():
    """For dict answers, the answer_text must be in 'Key: value\\nKey: value'
    format because that's what the M1.3 verifier's _extract_dict() parses."""
    _need_bench()

    def factory(call_n, contents):
        if call_n == 1:
            return json.dumps(
                {
                    "interpretation": "x",
                    "needed_columns": ["Fund"],
                    "expected_answer_type": "dict",
                    "expected_output_shape": "one entry per fund",
                    "subgoals": [
                        {
                            "step_number": 1,
                            "description": "x",
                            "operation": "groupby",
                            "pandas_hint": None,
                        }
                    ],
                    "notes": "",
                }
            )
        return "__result__ = df.groupby('Fund').size().to_dict()"

    client = _MockClient(factory)
    sandbox = LocalSandbox(allow_unsafe=True)
    solver = AgentSolver(client=client, sandbox=sandbox)

    result = asyncio.run(
        solver.solve(
            BENCH_FILES / "synthetic1_A.xlsx",
            "How many companies per fund?",
            "dict",
        )
    )
    assert result.error is None
    # Verifier parses 'Fund I: 8\nFund II: 11\n...' style
    assert "Fund I:" in result.answer_text
    assert "\n" in result.answer_text


def test_agent_solver_emits_list_in_verifier_format():
    """For list answers, comma-separated — what the verifier's _extract_items expects."""
    _need_bench()

    def factory(call_n, contents):
        if call_n == 1:
            return json.dumps(
                {
                    "interpretation": "x",
                    "needed_columns": ["Company"],
                    "expected_answer_type": "list",
                    "expected_output_shape": "a flat list of names",
                    "subgoals": [
                        {
                            "step_number": 1,
                            "description": "x",
                            "operation": "lookup",
                            "pandas_hint": None,
                        }
                    ],
                    "notes": "",
                }
            )
        return "__result__ = df['Company'].head(5).tolist()"

    client = _MockClient(factory)
    sandbox = LocalSandbox(allow_unsafe=True)
    solver = AgentSolver(client=client, sandbox=sandbox)

    result = asyncio.run(
        solver.solve(
            BENCH_FILES / "synthetic1_A.xlsx",
            "First 5 companies?",
            "list",
        )
    )
    assert result.error is None
    assert "," in result.answer_text
    # Should NOT contain '\n' for a flat list
    assert "\n" not in result.answer_text
