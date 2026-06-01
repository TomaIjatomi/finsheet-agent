"""
Typed structures passed between agents.

SchemaCard      — Schema Agent's output. The "what's in this workbook" sheet.
                  Consumed by Decomposition (planning) and Computation (codegen).
QueryPlan       — Decomposition Agent's output. The ordered subgoals the
                  Computation Agent will execute via the MCP execute_python tool.
AgentResult     — Wrapper for any agent's output, with timing + token + cost.

All types are Pydantic models so they double as response schemas for
google-genai's controlled-generation (response_schema=) feature, which gives
us reliable JSON output from the Decomposition Agent without prompt-side
JSON instructions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---- Schema Card --------------------------------------------------------


class ColumnSummary(BaseModel):
    """One column in the workbook as the agent stack sees it."""

    name: str = Field(
        ...,
        description="Canonical column name. Multi-line headers are collapsed to single line; "
        "agents MUST refer to columns by this exact name (e.g. 'Entry Enterprise "
        "Value' not 'Entry EV').",
    )
    col_letter: str
    dtype: Literal["string", "number", "date", "mixed", "empty"]


class FundSummary(BaseModel):
    """One fund's row span in the workbook."""

    fund: str
    start_row: int
    end_row: int
    n_companies: int


class SchemaCard(BaseModel):
    """Everything the planner and computation agents need to know about
    a workbook without seeing the cells. Produced once per (file, sheet)
    and reused across the query.
    """

    file_path: str
    sheet_name: str
    n_rows: int
    n_cols: int
    header_row: int
    data_start_row: int
    data_end_row: int
    data_range: str = Field(
        ...,
        description="The Excel range covering the header row through the last data row. "
        "Pass this as the named-range spec to execute_python.",
    )
    columns: list[ColumnSummary]
    fund_layout: Literal["column", "row_separator", "unknown"]
    fund_column: str | None = Field(
        None, description="Column letter of the Fund column when fund_layout=='column'."
    )
    funds: list[FundSummary]
    average_rows: list[int] = Field(
        default_factory=list,
        description="Row numbers (1-indexed) that are average/summary rows, NOT actual "
        "portfolio companies. Pandas code must exclude these when aggregating.",
    )
    structural_notes: list[str] = Field(default_factory=list)


# ---- Query Plan ---------------------------------------------------------


class Subgoal(BaseModel):
    """One discrete pandas operation in the computation plan."""

    step_number: int
    description: str = Field(
        ...,
        description="Plain-English description of this step (e.g. 'Filter rows where "
        "Status equals Unrealized').",
    )
    operation: Literal[
        "filter",
        "groupby",
        "aggregate",
        "sort",
        "count",
        "lookup",
        "compute",
        "transform",
        "other",
    ]
    pandas_hint: str | None = Field(
        None,
        description="Optional pandas expression hint, NOT executable code. The "
        "Computation Agent in M2.3 writes the actual code.",
    )


AnswerType = Literal["numeric", "string", "list", "dict", "bool", "date"]


class QueryPlan(BaseModel):
    """Decomposition Agent's output. The Computation Agent consumes this
    to generate pandas code per subgoal."""

    interpretation: str = Field(
        ..., description="One-sentence restatement of what the user is actually asking."
    )
    needed_columns: list[str] = Field(
        ...,
        description="Subset of SchemaCard.columns this query touches. Exact column names "
        "from the schema — multi-line headers in their collapsed form.",
    )
    expected_answer_type: AnswerType = Field(
        ...,
        description="Maps to the verifier's answer_type. 'dict' means one entry per fund "
        "(or per group); 'list' means a flat ordered list of values.",
    )
    expected_output_shape: str = Field(
        ...,
        description="Natural-language description of what the answer should look like "
        "(e.g. 'one number per fund, 8 entries total').",
    )
    subgoals: list[Subgoal]
    notes: str = Field(
        default="", description="Caveats, assumptions, or warnings for downstream agents."
    )


# ---- Generic agent envelope --------------------------------------------


class AgentResult(BaseModel):
    """Wrapper for any agent's output, with execution metadata."""

    agent: str
    output: dict
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    error: str | None = None


# ---- Fact Sheet (M2.3) --------------------------------------------------


class FactSheetEntry(BaseModel):
    """One result from one subgoal execution. The architectural commitment:
    every number that ends up in the final answer comes from one of these,
    and every entry carries the code that produced it for full traceability.
    """

    subgoal_step: int
    subgoal_description: str
    code: str = Field(
        ...,
        description="The full Python code executed in the sandbox, INCLUDING the prelude. "
        "Reproducible by passing to the same execute_python tool.",
    )
    value: object | None = Field(
        None, description="The serialized result from the sandbox (the __result__ value)."
    )
    citation: str | None = Field(
        None, description="Cell-range citation produced by cite_cells, e.g. '[Portfolio!I6:I179]'."
    )
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    attempts: int = 1
    latency_ms: int = 0


class FactSheet(BaseModel):
    """The accumulating record of computed values for one question.
    Consumed by the Synthesizer (and, later, the Verifier) to produce the
    final answer.
    """

    file_path: str
    sheet_name: str
    question: str
    entries: list[FactSheetEntry] = Field(default_factory=list)
    failed: bool = False

    def add(self, entry: FactSheetEntry) -> None:
        self.entries.append(entry)
        if entry.error:
            self.failed = True

    def last_value(self) -> object | None:
        if not self.entries:
            return None
        return self.entries[-1].value


class ComputationResult(BaseModel):
    """ComputationAgent's full output: the Fact Sheet + final formatted answer."""

    fact_sheet: FactSheet
    final_answer: object | None
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    n_subgoals_attempted: int = 0
    n_subgoals_succeeded: int = 0
    n_retries: int = 0
    error: str | None = None
