"""Tests for M2.3 — ComputationAgent + prelude + FactSheet.

Uses LocalSandbox (the unsafe-for-LLM-output sandbox) for real pandas
execution so we can verify the prelude is structurally correct against
real bench files. The Gemini client is mocked — tests don't require GCP.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from finsheet.agents import (
    ComputationAgent,
    QueryPlan,
    SchemaAgent,
    Subgoal,
    build_prelude,
)
from finsheet.agents.computation import _format_final_answer
from finsheet.agents.types import FactSheet, FactSheetEntry
from finsheet.mcp.sandbox import LocalSandbox
from finsheet.mcp.server import tool_execute_python

BENCH_FILES = Path("bench/data/files")


def _need_bench():
    if not BENCH_FILES.exists():
        pytest.skip("Bench not built")


# ---- Prelude ----------------------------------------------------------


def test_prelude_for_column_layout_omits_fund_injection():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    assert card.fund_layout == "column"
    prelude = build_prelude(card)
    assert "row_separator" not in prelude.lower() or "Layout is 'column'" in prelude
    assert "_fund_for_pandas_idx" not in prelude
    # Average-row filter always present
    assert "Average" in prelude


def test_prelude_for_row_separator_injects_fund_column():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic4_A.xlsx")
    assert card.fund_layout == "row_separator"
    prelude = build_prelude(card)
    assert "_fund_for_pandas_idx" in prelude
    assert "FUND_BOUNDARIES" in prelude
    assert f"DATA_START_ROW = {card.data_start_row}" in prelude
    # Each fund name must appear
    for f in card.funds:
        assert f.fund in prelude


def test_prelude_executes_without_error_on_row_separator():
    """The prelude is the orchestrator's deterministic code. It MUST run
    cleanly against the real bench files or the whole architecture breaks."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic4_A.xlsx")
    prelude = build_prelude(card)
    sandbox = LocalSandbox(allow_unsafe=True)
    # Just run the prelude and inspect df state
    test_code = (
        prelude
        + "\n__result__ = {"
        + "'n_rows': len(df), "
        + "'cols': list(df.columns), "
        + "'fund_values': sorted(df['Fund'].dropna().unique().tolist())"
        + "}"
    )
    result = tool_execute_python(
        file_path=str(BENCH_FILES / "synthetic4_A.xlsx"),
        code=test_code,
        named_ranges={"df": {"sheet": "Portfolio", "range": card.data_range}},
        sandbox=sandbox,
    )
    assert result["error"] is None, f"prelude failed: {result['error']}"
    # 152 actual companies, all 8 funds labeled
    assert result["result"]["n_rows"] == 152
    assert "Fund" in result["result"]["cols"]
    assert set(result["result"]["fund_values"]) == {f.fund for f in card.funds}


def test_prelude_executes_cleanly_on_column_layout():
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    prelude = build_prelude(card)
    sandbox = LocalSandbox(allow_unsafe=True)
    result = tool_execute_python(
        file_path=str(BENCH_FILES / "synthetic1_A.xlsx"),
        code=prelude + "\n__result__ = len(df)",
        named_ranges={"df": {"sheet": "Portfolio", "range": card.data_range}},
        sandbox=sandbox,
    )
    assert result["error"] is None
    # synthetic1 has 45 companies; no average rows in this template
    assert result["result"] == 45


# ---- Format final answer ----------------------------------------------


def _fs_with_value(v):
    return FactSheet(
        file_path="x",
        sheet_name="x",
        question="x",
        entries=[
            FactSheetEntry(
                subgoal_step=1,
                subgoal_description="d",
                code="c",
                value=v,
            )
        ],
    )


def test_format_final_answer_numeric_from_int():
    fs = _fs_with_value(42)
    assert _format_final_answer(fs, "numeric") == 42.0


def test_format_final_answer_numeric_from_singleton_dict():
    """LLM may return {'total': 42} when a number was asked for."""
    fs = _fs_with_value({"total": 42.5})
    assert _format_final_answer(fs, "numeric") == 42.5


def test_format_final_answer_list_passthrough():
    fs = _fs_with_value(["a", "b", "c"])
    assert _format_final_answer(fs, "list") == ["a", "b", "c"]


def test_format_final_answer_list_from_dict_values():
    """Some plans produce dicts when the user asked for an ordered list."""
    fs = _fs_with_value({"Apex": 10, "Bloom": 20})
    assert _format_final_answer(fs, "list") == [10, 20]


def test_format_final_answer_dict_passthrough():
    fs = _fs_with_value({"Fund I": 100, "Fund II": 200})
    assert _format_final_answer(fs, "dict") == {"Fund I": 100, "Fund II": 200}


def test_format_final_answer_dict_rejects_non_dict():
    fs = _fs_with_value(42)
    assert _format_final_answer(fs, "dict") is None


def test_format_final_answer_bool_from_int():
    assert _format_final_answer(_fs_with_value(1), "bool") is True
    assert _format_final_answer(_fs_with_value(0), "bool") is False


def test_format_final_answer_string_passthrough():
    fs = _fs_with_value("Apex Holdings")
    assert _format_final_answer(fs, "string") == "Apex Holdings"


def test_format_final_answer_handles_failed_fact_sheet():
    fs = FactSheet(
        file_path="x",
        sheet_name="x",
        question="x",
        entries=[
            FactSheetEntry(
                subgoal_step=1,
                subgoal_description="d",
                code="c",
                value=None,
                error="KeyError: nope",
            )
        ],
    )
    assert _format_final_answer(fs, "numeric") is None


# ---- ComputationAgent end-to-end with mocked client ------------------


class _MockClient:
    """Mock google-genai client that returns canned code per call."""

    def __init__(self, code_factory):
        self._code_factory = code_factory
        self.calls: list[dict] = []
        self.aio = SimpleNamespace(models=self)

    async def generate_content(self, model, contents, config):  # noqa: ARG002
        self.calls.append({"model": model, "contents": contents})
        code = self._code_factory(len(self.calls), contents)
        return SimpleNamespace(
            text=code,
            usage_metadata=SimpleNamespace(
                prompt_token_count=2000,
                candidates_token_count=120,
            ),
        )


def _execute_python_with_local_sandbox():
    """Returns an execute_python_fn that uses LocalSandbox."""
    sandbox = LocalSandbox(allow_unsafe=True)

    def fn(file_path, code, named_ranges, timeout_s=30):  # noqa: ARG001
        return tool_execute_python(
            file_path=file_path,
            code=code,
            named_ranges=named_ranges,
            sandbox=sandbox,
        )

    return fn


def test_computation_agent_simple_count():
    """End-to-end: 1 subgoal that counts companies → numeric result."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    plan = QueryPlan(
        interpretation="Count all portfolio companies.",
        needed_columns=["Company"],
        expected_answer_type="numeric",
        expected_output_shape="A single integer.",
        subgoals=[
            Subgoal(
                step_number=1,
                description="Count rows",
                operation="count",
                pandas_hint="len(df)",
            )
        ],
    )
    client = _MockClient(lambda n, c: "__result__ = len(df)")
    agent = ComputationAgent(
        client=client,
        execute_python_fn=_execute_python_with_local_sandbox(),
    )
    result = asyncio.run(agent.execute(plan, card))
    assert result.error is None
    assert result.final_answer == 45.0
    assert result.n_subgoals_succeeded == 1
    assert result.n_retries == 0


def test_computation_agent_per_fund_aggregation_on_row_separator():
    """The hardest case: row_separator layout, per-fund aggregation.
    Verifies prelude's Fund-column injection works end-to-end."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic4_A.xlsx")
    plan = QueryPlan(
        interpretation="Count companies per fund.",
        needed_columns=["Fund", "Company"],
        expected_answer_type="dict",
        expected_output_shape="One integer per fund (8 funds expected).",
        subgoals=[
            Subgoal(
                step_number=1,
                description="Count by fund",
                operation="groupby",
            )
        ],
    )
    client = _MockClient(lambda n, c: "__result__ = df.groupby('Fund').size().to_dict()")
    agent = ComputationAgent(
        client=client,
        execute_python_fn=_execute_python_with_local_sandbox(),
    )
    result = asyncio.run(agent.execute(plan, card))
    assert result.error is None
    assert isinstance(result.final_answer, dict)
    assert len(result.final_answer) == 8  # 8 funds
    assert sum(result.final_answer.values()) == 152
    # Each fund name from the schema appears
    for f in card.funds:
        assert f.fund in result.final_answer


def test_computation_agent_retries_on_failure():
    """LLM emits broken code, then fixed code on retry."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    plan = QueryPlan(
        interpretation="Count companies.",
        needed_columns=["Company"],
        expected_answer_type="numeric",
        expected_output_shape="int",
        subgoals=[
            Subgoal(
                step_number=1,
                description="Count",
                operation="count",
            )
        ],
    )

    def code_for(call_n, contents):
        if call_n == 1:
            return "__result__ = df['NonexistentColumn'].sum()"  # KeyError
        return "__result__ = len(df)"

    client = _MockClient(code_for)
    agent = ComputationAgent(
        client=client,
        execute_python_fn=_execute_python_with_local_sandbox(),
        max_retries=2,
    )
    result = asyncio.run(agent.execute(plan, card))
    assert result.error is None
    assert result.final_answer == 45.0
    assert result.n_retries == 1
    assert result.fact_sheet.entries[0].attempts >= 1


def test_computation_agent_gives_up_after_max_retries():
    """All attempts emit broken code → agent surfaces the failure."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    plan = QueryPlan(
        interpretation="x",
        needed_columns=["Company"],
        expected_answer_type="numeric",
        expected_output_shape="x",
        subgoals=[Subgoal(step_number=1, description="x", operation="count")],
    )
    client = _MockClient(lambda n, c: "__result__ = df['Nope'].sum()")
    agent = ComputationAgent(
        client=client,
        execute_python_fn=_execute_python_with_local_sandbox(),
        max_retries=2,
    )
    result = asyncio.run(agent.execute(plan, card))
    assert result.error is not None
    assert result.fact_sheet.failed
    assert result.final_answer is None


def test_computation_agent_strips_markdown_fences():
    """LLM sometimes wraps code in ```python ... ``` despite the prompt."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    plan = QueryPlan(
        interpretation="x",
        needed_columns=["Company"],
        expected_answer_type="numeric",
        expected_output_shape="x",
        subgoals=[Subgoal(step_number=1, description="x", operation="count")],
    )
    client = _MockClient(lambda n, c: "```python\n__result__ = len(df)\n```")
    agent = ComputationAgent(
        client=client,
        execute_python_fn=_execute_python_with_local_sandbox(),
    )
    result = asyncio.run(agent.execute(plan, card))
    assert result.error is None
    assert result.final_answer == 45.0


def test_computation_agent_propagates_codegen_exception():
    """If Gemini call itself fails, the subgoal records the error."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    plan = QueryPlan(
        interpretation="x",
        needed_columns=["Company"],
        expected_answer_type="numeric",
        expected_output_shape="x",
        subgoals=[Subgoal(step_number=1, description="x", operation="count")],
    )

    class _Boom:
        def __init__(self):
            self.aio = SimpleNamespace(models=self)

        async def generate_content(self, **kw):
            raise ConnectionError("simulated GCP outage")

    agent = ComputationAgent(
        client=_Boom(),
        execute_python_fn=_execute_python_with_local_sandbox(),
        max_retries=1,
    )
    result = asyncio.run(agent.execute(plan, card))
    assert result.error is not None
    assert result.fact_sheet.failed


def test_computation_agent_filter_then_aggregate_preserves_filter():
    """Regression test for the Q11 bug (D18): a two-subgoal plan that
    filters then aggregates must produce a result reflecting the filter.

    With the old per-subgoal pattern, step 2's code would operate on the
    full unfiltered df, throwing away step 1's filter. With single-codegen,
    the LLM is forced to chain operations into one block.
    """
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic4_A.xlsx")
    plan = QueryPlan(
        interpretation="Sum Entry Enterprise Value for Unrealized companies, per fund.",
        needed_columns=["Status", "Entry Enterprise Value", "Fund"],
        expected_answer_type="dict",
        expected_output_shape="One number per fund.",
        subgoals=[
            Subgoal(
                step_number=1,
                description="Filter to Unrealized companies",
                operation="filter",
                pandas_hint="df[df['Status']=='Unrealized']",
            ),
            Subgoal(
                step_number=2,
                description="Sum Entry Enterprise Value per fund",
                operation="aggregate",
            ),
        ],
    )
    chained = (
        "__result__ = (df[df['Status']=='Unrealized']"
        ".groupby('Fund')['Entry Enterprise Value'].sum().to_dict())"
    )
    client = _MockClient(lambda n, c: chained)
    agent = ComputationAgent(
        client=client,
        execute_python_fn=_execute_python_with_local_sandbox(),
    )
    result = asyncio.run(agent.execute(plan, card))
    assert result.error is None
    assert len(client.calls) == 1
    assert isinstance(result.final_answer, dict)
    if "Fund V" in result.final_answer:
        assert result.final_answer["Fund V"] < 5000, (
            f"Fund V sum looks unfiltered ({result.final_answer['Fund V']}). "
            f"This is the regression Q11 was failing on."
        )


def test_computation_agent_passes_full_plan_to_codegen():
    """Single-codegen design (post-D18): the LLM sees ALL subgoals in one
    prompt and generates one block of code. Verifies the user prompt
    contains every subgoal."""
    _need_bench()
    card = SchemaAgent().profile(BENCH_FILES / "synthetic1_A.xlsx")
    plan = QueryPlan(
        interpretation="Count then describe.",
        needed_columns=["Company"],
        expected_answer_type="numeric",
        expected_output_shape="A number.",
        subgoals=[
            Subgoal(step_number=1, description="Filter portfolio companies", operation="filter"),
            Subgoal(step_number=2, description="Count rows", operation="count"),
        ],
    )
    client = _MockClient(lambda n, c: "__result__ = len(df)")
    agent = ComputationAgent(
        client=client,
        execute_python_fn=_execute_python_with_local_sandbox(),
    )
    result = asyncio.run(agent.execute(plan, card))
    assert result.error is None
    # Single-codegen design: one FactSheetEntry per question, one Gemini call
    assert len(result.fact_sheet.entries) == 1
    assert len(client.calls) == 1
    # The single prompt must contain BOTH subgoals
    prompt = client.calls[0]["contents"]
    assert "Filter portfolio companies" in prompt
    assert "Count rows" in prompt
    # Final answer is correct
    assert result.final_answer == 45.0
