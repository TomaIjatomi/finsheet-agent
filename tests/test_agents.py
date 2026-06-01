"""Tests for M2.2 — SchemaAgent + DecompositionAgent.

SchemaAgent runs against the real synthetic bench (no GCP needed —
it's deterministic openpyxl + pandas).

DecompositionAgent is tested with a mocked google-genai client that
returns canned QueryPlan JSON, so tests don't require Vertex AI access.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from finsheet.agents import (
    DecompositionAgent,
    QueryPlan,
    SchemaAgent,
    SchemaCard,
    Subgoal,
)

BENCH_FILES = Path("bench/data/files")


def _need_bench():
    if not BENCH_FILES.exists():
        pytest.skip("Bench not built")


# ---- SchemaAgent: deterministic, runs against real bench files --------


def test_schema_agent_column_layout():
    """synthetic1 — fund_layout='column'."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    assert isinstance(card, SchemaCard)
    assert card.fund_layout == "column"
    assert card.fund_column is not None
    assert len(card.funds) == 4
    assert sum(f.n_companies for f in card.funds) == 45


def test_schema_agent_row_separator_layout():
    """synthetic4 — fund_layout='row_separator', multi-line headers."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic4_A.xlsx")
    assert card.fund_layout == "row_separator"
    assert card.fund_column is None
    assert len(card.funds) == 8
    assert sum(f.n_companies for f in card.funds) == 152
    # Multi-line header collapsed:
    col_names = [c.name for c in card.columns]
    assert "Entry Enterprise Value" in col_names
    assert "Entry EV" not in col_names  # collapsed form ONLY


def test_schema_agent_data_range_is_excel_format():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    # data_range should look like "A4:M50" — header letter, header row,
    # last col letter, last data row
    assert card.data_range.startswith("A")
    assert ":" in card.data_range
    # Header row is included
    assert f"A{card.header_row}" in card.data_range


def test_schema_agent_emits_structural_notes_for_row_separator():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic4_A.xlsx")
    notes_blob = " ".join(card.structural_notes)
    assert "row_separator" in notes_blob.lower() or "no 'Fund' column" in notes_blob.lower()
    # Must warn about multi-line collapsed headers
    assert "Entry Enterprise Value" in notes_blob or "multi-line" in notes_blob.lower()


def test_schema_agent_flags_average_rows():
    """synthetic2 has 'Fund X Average' rows after each fund block."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic2_A.xlsx")
    assert len(card.average_rows) >= 3
    notes_blob = " ".join(card.structural_notes)
    assert "average" in notes_blob.lower()


def test_schema_card_round_trips_through_json():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    js = card.model_dump_json()
    restored = SchemaCard.model_validate_json(js)
    assert restored == card


def test_schema_agent_file_not_found_raises():
    with pytest.raises(ValueError, match="schema introspection failed"):
        SchemaAgent().profile("/nonexistent/file.xlsx")


# ---- DecompositionAgent: mocked Gemini client --------------------------


class _MockGenAIClient:
    """Mimics google-genai's Client.aio.models.generate_content."""

    def __init__(self, response_factory):
        self._response_factory = response_factory
        self.calls: list[dict] = []
        self.aio = SimpleNamespace(models=self)

    async def generate_content(self, model, contents, config):  # noqa: ARG002
        self.calls.append({"model": model, "contents": contents})
        payload = self._response_factory(contents)
        return SimpleNamespace(
            text=json.dumps(payload) if isinstance(payload, dict) else payload,
            usage_metadata=SimpleNamespace(
                prompt_token_count=1200,
                candidates_token_count=180,
            ),
        )


def _canned_plan_per_fund_sum() -> dict:
    """Reasonable plan for 'total unrealized capital per fund'."""
    return {
        "interpretation": "Sum the Entry Enterprise Value of every Unrealized "
        "company, grouped by fund.",
        "needed_columns": ["Status", "Entry Enterprise Value"],
        "expected_answer_type": "dict",
        "expected_output_shape": "One numeric entry per fund.",
        "subgoals": [
            {
                "step_number": 1,
                "description": "Filter to Unrealized companies",
                "operation": "filter",
                "pandas_hint": "df[df['Status']=='Unrealized']",
            },
            {
                "step_number": 2,
                "description": "Group by fund (use fund_boundaries since layout is row_separator)",
                "operation": "groupby",
                "pandas_hint": None,
            },
            {
                "step_number": 3,
                "description": "Sum Entry Enterprise Value per group",
                "operation": "aggregate",
                "pandas_hint": "['Entry Enterprise Value'].sum()",
            },
        ],
        "notes": "Excludes average/summary rows automatically when filtering on Status.",
    }


def test_decomposition_returns_typed_plan():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic4_A.xlsx")
    client = _MockGenAIClient(lambda c: _canned_plan_per_fund_sum())
    agent = DecompositionAgent(client=client, model="gemini-2.5-pro")

    plan, result = asyncio.run(
        agent.plan(
            "What is the total unrealized capital per fund?",
            card,
        )
    )
    assert plan is not None
    assert isinstance(plan, QueryPlan)
    assert plan.expected_answer_type == "dict"
    assert len(plan.subgoals) == 3
    assert plan.subgoals[0].operation == "filter"
    assert result.error is None
    assert result.tokens_in == 1200
    assert result.cost_usd > 0


def test_decomposition_passes_schema_to_llm():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic4_A.xlsx")
    client = _MockGenAIClient(lambda c: _canned_plan_per_fund_sum())
    agent = DecompositionAgent(client=client)
    asyncio.run(agent.plan("Q", card))
    # Verify the prompt included the schema (column names should appear)
    prompt_text = client.calls[0]["contents"]
    assert "Entry Enterprise Value" in prompt_text
    assert "row_separator" in prompt_text


def test_decomposition_handles_llm_returning_invalid_json():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    client = _MockGenAIClient(lambda c: "this is not json at all")
    agent = DecompositionAgent(client=client)
    plan, result = asyncio.run(agent.plan("Q", card))
    assert plan is None
    assert result.error is not None
    assert "parse failed" in result.error.lower()


def test_decomposition_handles_partial_plan():
    """Pydantic must reject a plan missing required fields."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    bad = {"interpretation": "x"}  # missing everything else
    client = _MockGenAIClient(lambda c: bad)
    agent = DecompositionAgent(client=client)
    plan, result = asyncio.run(agent.plan("Q", card))
    assert plan is None
    assert result.error is not None


def test_decomposition_propagates_client_exception():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")

    class _Boom:
        aio = None

        def __init__(self):
            self.aio = SimpleNamespace(models=self)

        async def generate_content(self, **_):
            raise ConnectionError("simulated outage")

    agent = DecompositionAgent(client=_Boom())
    plan, result = asyncio.run(agent.plan("Q", card))
    assert plan is None
    assert "ConnectionError" in result.error
    assert "simulated outage" in result.error


def test_plan_round_trips_through_json():
    plan = QueryPlan(
        interpretation="x",
        needed_columns=["A"],
        expected_answer_type="numeric",
        expected_output_shape="one number",
        subgoals=[Subgoal(step_number=1, description="d", operation="filter")],
        notes="",
    )
    restored = QueryPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan


def test_decomposition_sample_question_categories():
    """Every one of the 7 question categories from M1.2 should produce a
    plan with a sensible answer_type — verifying our mock prompt+schema
    pipeline doesn't drop fields."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    cases = [
        ("How many funds are there?", "numeric"),
        ("Which fund is the latest?", "string"),
        ("List all companies in the newest fund.", "list"),
        ("How many companies are in each fund?", "dict"),
        ("Are there any unrealized companies?", "bool"),
        ("What is the entry date of Apex Holdings?", "date"),
    ]
    for question, expected_type in cases:
        canned = {
            "interpretation": "test",
            "needed_columns": ["Company"],
            "expected_answer_type": expected_type,
            "expected_output_shape": "test",
            "subgoals": [
                {
                    "step_number": 1,
                    "description": "test",
                    "operation": "lookup",
                    "pandas_hint": None,
                }
            ],
            "notes": "",
        }
        client = _MockGenAIClient(lambda c, p=canned: p)
        agent = DecompositionAgent(client=client)
        plan, result = asyncio.run(agent.plan(question, card))
        assert plan is not None, f"Failed on: {question} (err={result.error})"
        assert plan.expected_answer_type == expected_type
