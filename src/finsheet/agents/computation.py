"""
Computation Agent (M2.3).

Takes a SchemaCard + QueryPlan + the MCP execute_python tool, and produces
a FactSheet with one entry per subgoal. Each entry contains the value
produced and the exact code that produced it — full traceability.

Flow per subgoal:
  1. Generate pandas code (Gemini 2.5 Pro, controlled-output-tokens).
  2. Prepend the deterministic prelude (Fund column + average-row filter).
  3. Call execute_python via MCP with the full code + the workbook's data_range.
  4. On error: retry up to max_retries with the error message in context.
  5. Append a FactSheetEntry recording outcome.

Architectural commitment from CoDaS: every value in the final answer comes
from a tool call. The LLM never invents numbers — it generates code, the
sandbox executes the code, the result lands in the Fact Sheet.

After all subgoals run, `format_final_answer()` casts the last entry's
value into the shape expected by the verifier (numeric/string/list/dict/
bool/date).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from .prelude import build_prelude
from .types import (
    ComputationResult,
    FactSheet,
    FactSheetEntry,
    QueryPlan,
    SchemaCard,
)

# Gemini 2.5 Pro pricing (per million tokens) — same constants as M2.2.
GEMINI_25_PRO_INPUT_PER_MTOK = 1.25
GEMINI_25_PRO_OUTPUT_PER_MTOK = 10.00


def _estimate_cost(tok_in: int, tok_out: int) -> float:
    return (
        tok_in * GEMINI_25_PRO_INPUT_PER_MTOK / 1_000_000
        + tok_out * GEMINI_25_PRO_OUTPUT_PER_MTOK / 1_000_000
    )


CODEGEN_SYSTEM_PROMPT = """You are a pandas code generator inside a private-equity
portfolio QA system.

You receive:
  - The SchemaCard describing the workbook (column names, fund layout, etc.)
  - The full QueryPlan: an ordered list of subgoals representing the logical
    decomposition of the question.

A DataFrame named `df` is already loaded. Before your code runs, an
AUTO-GENERATED PRELUDE has already executed which:
  - Injected a `Fund` column if the layout is row_separator
  - Removed all non-company rows (average/summary rows AND fund-divider rows)

You can therefore ALWAYS rely on:
  - `df['Fund']` exists and is populated for every row
  - `df` contains only portfolio companies (no average rows, no dividers)
  - `df.index` is 0..N-1 (reset)

YOUR JOB
Generate a SINGLE block of Python code that performs ALL the subgoals in
sequence, chaining operations through pandas. Each subgoal in the plan is
a logical step in your reasoning — translate the ordered subgoals into ONE
chained pandas expression (or a few statements if a chain isn't natural).
Set the final answer to `__result__`.

RULES
1. Use column names EXACTLY as listed in the schema. Multi-line headers
   are collapsed (e.g. 'Entry Enterprise Value', NOT 'Entry EV').
2. Subgoals are SEQUENTIAL — each one operates on the result of the previous
   step. When the plan says "filter then aggregate", your code applies the
   filter BEFORE the aggregation. Do not lose intermediate filters.
3. For dict answers (one entry per fund/group), end with .to_dict()
4. For list answers, end with .tolist()
5. For numeric answers, ensure __result__ is a plain Python float or int.
6. For "newest fund" / "latest fund" questions: the fund with the highest
   roman numeral or sequence number is the newest (Fund VIII > Fund I).
   Use the schema's `funds` list to identify it by name.
7. For lookups by company name: use exact-match equality (`df['Company'] == 'X'`).
   If multiple rows match, return the first by default.
8. Keep code MINIMAL — typically 1 to 4 lines.
9. Return ONLY Python code. No markdown fences, no commentary, no explanation.

EXAMPLE 1 — "Total unrealized capital per fund":
  Subgoals:
    1. Filter to Unrealized companies
    2. Group by Fund and sum Entry Enterprise Value
  Code:
    __result__ = (df[df['Status']=='Unrealized']
                  .groupby('Fund')['Entry Enterprise Value'].sum().to_dict())

EXAMPLE 2 — "Companies in the newest fund":
  Subgoals:
    1. Identify the newest fund (Fund VIII)
    2. Filter rows to that fund
    3. Extract the company list
  Code:
    __result__ = df[df['Fund']=='Fund VIII']['Company'].tolist()

EXAMPLE 3 — "Median net debt/EBITDA across all investments":
  Subgoals:
    1. Compute the Net Debt / EBITDA ratio per company
    2. Take the median
  Code:
    ratios = df['Net Debt at Entry'] / df['Entry EBITDA']
    __result__ = float(ratios[ratios.notna() & ~ratios.isin([float('inf'), float('-inf')])].median())"""


CODEGEN_FIX_SYSTEM_PROMPT = (
    CODEGEN_SYSTEM_PROMPT
    + """

This is a RETRY: your previous code failed in the sandbox. You will be
shown the failing code and the exact error message. Produce CORRECTED code
that addresses the specific error. Do not re-explain — just emit the fixed
code."""
)


def _format_subgoals_for_prompt(plan: QueryPlan) -> str:
    """Render subgoals as a numbered list for the codegen prompt."""
    lines = []
    for sg in plan.subgoals:
        line = f"  {sg.step_number}. [{sg.operation}] {sg.description}"
        if sg.pandas_hint:
            line += f"\n     hint: {sg.pandas_hint}"
        lines.append(line)
    return "\n".join(lines)


def _build_codegen_user_prompt(
    plan: QueryPlan,
    schema_card: SchemaCard,
) -> str:
    return (
        f"SCHEMA:\n{schema_card.model_dump_json(indent=2)}\n\n"
        f"QUERY INTERPRETATION:\n{plan.interpretation}\n\n"
        f"EXPECTED ANSWER TYPE: {plan.expected_answer_type}\n"
        f"EXPECTED OUTPUT SHAPE: {plan.expected_output_shape}\n\n"
        f"SUBGOALS (perform ALL in sequence):\n"
        f"{_format_subgoals_for_prompt(plan)}\n\n"
        f"Generate ONE block of code that performs all subgoals and "
        f"sets __result__ to the final answer."
    )


def _build_fix_user_prompt(
    plan: QueryPlan,
    schema_card: SchemaCard,
    last_code: str,
    last_error: str,
) -> str:
    return (
        _build_codegen_user_prompt(plan, schema_card)
        + "\n\nYOUR PREVIOUS CODE (failed):\n"
        + last_code
        + "\n\nERROR:\n"
        + last_error
        + "\n\nProduce corrected code."
    )


def _strip_markdown_fences(code: str) -> str:
    """Some Gemini outputs wrap code in ```python ... ``` despite our prompt."""
    s = code.strip()
    if s.startswith("```"):
        # Drop the first line and any trailing ```
        lines = s.splitlines()
        lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


def _format_final_answer(fact_sheet: FactSheet, expected_type: str) -> object | None:
    """Cast the last successful subgoal's value into the verifier's expected shape."""
    if not fact_sheet.entries:
        return None
    last = fact_sheet.entries[-1]
    if last.error:
        return None
    v = last.value

    if v is None:
        return None

    if expected_type == "numeric":
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.replace(",", "").strip())
            except ValueError:
                return None
        if isinstance(v, dict) and len(v) == 1:
            inner = next(iter(v.values()))
            return float(inner) if isinstance(inner, (int, float)) else None
        return None

    if expected_type == "string":
        if isinstance(v, str):
            return v
        if isinstance(v, list) and v:
            return str(v[0])
        return str(v)

    if expected_type == "list":
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return list(v.values())
        return [v]

    if expected_type == "dict":
        return v if isinstance(v, dict) else None

    if expected_type == "bool":
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in ("true", "yes", "y", "1")
        return None

    if expected_type == "date":
        return str(v)

    return v


class ComputationAgent:
    """Generates and executes pandas code per subgoal, building a Fact Sheet."""

    name = "computation_agent"

    def __init__(
        self,
        client,
        execute_python_fn: Callable,
        model: str = "gemini-2.5-pro",
        max_retries: int = 2,
        max_output_tokens: int = 2048,
        temperature: float = 0.0,
        sandbox_timeout_s: int = 30,
    ):
        """
        Args:
            client: google-genai Client (real or mocked).
            execute_python_fn: a callable matching the signature of
                `tool_execute_python(file_path, code, named_ranges, sandbox=...)`
                The sandbox must be bound in by the caller — Computation Agent
                doesn't construct sandboxes.
            max_retries: how many times to retry a failing subgoal with the
                error fed back to the LLM.
        """
        self._client = client
        self._execute_python = execute_python_fn
        self._model = model
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._sandbox_timeout_s = sandbox_timeout_s

    async def _gen_code(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, int, int, int]:
        """One Gemini call. Returns (code, tokens_in, tokens_out, latency_ms)."""
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )
        start = time.perf_counter()
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=config,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        usage = response.usage_metadata
        tok_in = (usage.prompt_token_count or 0) if usage else 0
        tok_out = (usage.candidates_token_count or 0) if usage else 0
        code = _strip_markdown_fences(response.text or "")
        return code, tok_in, tok_out, latency_ms

    async def execute(
        self,
        plan: QueryPlan,
        schema_card: SchemaCard,
    ) -> ComputationResult:
        """Single-codegen approach: one Gemini call generates the full pandas
        block for the question, executed in one sandbox call. The plan's
        subgoals are passed as prompt context — they guide the LLM's reasoning
        but don't become separate sandbox calls (the per-subgoal pattern lost
        filter state between calls, see D17 in decisions.md).

        Returns a ComputationResult with a Fact Sheet containing one entry
        per question."""
        fact_sheet = FactSheet(
            file_path=schema_card.file_path,
            sheet_name=schema_card.sheet_name,
            question=plan.interpretation,
        )
        prelude = build_prelude(schema_card)

        total_tok_in = total_tok_out = total_lat_ms = 0
        total_cost = 0.0
        n_retries = 0

        named_ranges = {
            "df": {"sheet": schema_card.sheet_name, "range": schema_card.data_range},
        }

        last_code: str = ""
        last_error: str | None = None
        result: dict = {"error": "no attempt made"}
        succeeded = False
        entry_start = time.perf_counter()

        for attempt in range(self._max_retries + 1):
            # Build prompt — fresh vs fix-retry
            if attempt == 0:
                sys_prompt = CODEGEN_SYSTEM_PROMPT
                user_prompt = _build_codegen_user_prompt(plan, schema_card)
            else:
                sys_prompt = CODEGEN_FIX_SYSTEM_PROMPT
                user_prompt = _build_fix_user_prompt(
                    plan,
                    schema_card,
                    last_code,
                    last_error or "",
                )
                n_retries += 1

            try:
                code, t_in, t_out, lat = await self._gen_code(sys_prompt, user_prompt)
                total_tok_in += t_in
                total_tok_out += t_out
                total_lat_ms += lat
                total_cost += _estimate_cost(t_in, t_out)
            except Exception as e:
                last_error = f"codegen exception: {type(e).__name__}: {e}"
                last_code = ""
                continue

            full_code = prelude + "\n# ----- LLM-GENERATED CODE -----\n" + code
            result = self._execute_python(
                file_path=schema_card.file_path,
                code=full_code,
                named_ranges=named_ranges,
                timeout_s=self._sandbox_timeout_s,
            )
            if result.get("error") is None:
                succeeded = True
                last_code = code
                break

            last_code = code
            last_error = result.get("error") or "unknown sandbox error"

        entry_latency_ms = int((time.perf_counter() - entry_start) * 1000)
        # Use the last subgoal's step number for the FactSheetEntry to keep
        # the audit trail intelligible; the entry represents the full chain.
        last_step = plan.subgoals[-1].step_number if plan.subgoals else 1
        last_desc = plan.subgoals[-1].description if plan.subgoals else "(empty plan)"
        fact_sheet.add(
            FactSheetEntry(
                subgoal_step=last_step,
                subgoal_description=last_desc,
                code=prelude + "\n# ----- LLM-GENERATED CODE -----\n" + last_code,
                value=result.get("result") if succeeded else None,
                stdout=result.get("stdout", "") or "",
                stderr=result.get("stderr", "") or "",
                error=None if succeeded else last_error,
                attempts=n_retries + 1,
                latency_ms=entry_latency_ms,
            )
        )

        final_answer = _format_final_answer(fact_sheet, plan.expected_answer_type)
        return ComputationResult(
            fact_sheet=fact_sheet,
            final_answer=final_answer,
            total_tokens_in=total_tok_in,
            total_tokens_out=total_tok_out,
            total_latency_ms=total_lat_ms,
            total_cost_usd=total_cost,
            n_subgoals_attempted=len(plan.subgoals),
            n_subgoals_succeeded=len(plan.subgoals) if succeeded else 0,
            n_retries=n_retries,
            error=None if succeeded else "computation failed after retries",
        )

    def execute_sync(self, plan: QueryPlan, schema_card: SchemaCard) -> ComputationResult:
        """Convenience sync wrapper for scripts and notebooks."""
        return asyncio.run(self.execute(plan, schema_card))
