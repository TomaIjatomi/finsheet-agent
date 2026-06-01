"""
Decomposition Agent — produces the QueryPlan.

Takes a user question + a SchemaCard (from the Schema Agent) and outputs
a QueryPlan: an ordered list of natural-language subgoals + metadata
about the expected answer shape.

The Computation Agent (M2.3) will then turn each subgoal into actual
pandas code and call execute_python via MCP.

Implementation note: uses google-genai's controlled generation
(response_schema=QueryPlan) rather than prompt-side JSON instructions.
This eliminates the "model returned non-JSON" failure mode that was the
top error in the M1.3 baseline before we bumped tokens. The Pydantic
model in types.py doubles as the schema — single source of truth.
"""

from __future__ import annotations

import asyncio
import json
import time

from .types import AgentResult, QueryPlan, SchemaCard

SYSTEM_PROMPT = """You are a query planner for a private-equity portfolio
spreadsheet QA system.

You receive:
  - A natural-language question from an analyst.
  - The structured schema of the workbook the question is about.

You output a QueryPlan: an ordered list of pandas-style computational
subgoals that a downstream Computation Agent will execute deterministically.

RULES
1. Use ONLY column names exactly as they appear in the schema. Many headers
   are multi-line in the source and have been collapsed to single line
   (e.g. "Entry Enterprise Value"). Do not abbreviate or substitute. If
   the question uses informal terms like "entry EV", map them to the
   canonical column name.
2. Plan subgoals as discrete pandas operations: filter, groupby, aggregate,
   sort, count, lookup, compute, transform.
3. Respect the schema's structural notes. If fund_layout is
   "row_separator", there is no Fund column; the Computation Agent must
   slice rows using the fund_boundaries instead of groupby.
4. Exclude average/summary rows from any aggregation — they are NOT
   portfolio companies.
5. Pick the expected_answer_type that matches the question's natural
   shape:
     - "numeric": one number (sum, mean, median, count of a single thing)
     - "string": one entity name (highest EBITDA, latest fund, etc.)
     - "list":   ordered list of entities (e.g. companies sorted by X)
     - "dict":   one value per fund/group (e.g. avg EV per fund)
     - "bool":   yes/no
     - "date":   one date
6. If the question is ambiguous, pick the most likely interpretation and
   state your assumption in `notes`.
7. Keep subgoals minimal — 2 to 5 steps is typical. Do not over-decompose.

Return only the QueryPlan — no commentary, no markdown."""


def _build_user_prompt(question: str, schema_card: SchemaCard) -> str:
    return (
        f"QUESTION:\n{question}\n\n"
        f"SCHEMA:\n{schema_card.model_dump_json(indent=2)}\n\n"
        "Produce the QueryPlan."
    )


# Pricing constants (per million tokens). Conservative — actual billing
# from GCP may differ slightly by region/tier.
GEMINI_25_PRO_INPUT_PER_MTOK = 1.25
GEMINI_25_PRO_OUTPUT_PER_MTOK = 10.00


def _estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return (
        tokens_in * GEMINI_25_PRO_INPUT_PER_MTOK / 1_000_000
        + tokens_out * GEMINI_25_PRO_OUTPUT_PER_MTOK / 1_000_000
    )


class DecompositionAgent:
    """Single-shot LLM call: question + schema -> QueryPlan.

    Uses google-genai controlled generation (response_schema=) so JSON
    parsing is reliable — no regex fallback, no schema-instruction in
    the prompt. The Pydantic model IS the schema.
    """

    name = "decomposition_agent"

    def __init__(
        self,
        client,
        model: str = "gemini-2.5-pro",
        max_output_tokens: int = 4096,
        temperature: float = 0.0,
    ):
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    async def plan(
        self, question: str, schema_card: SchemaCard
    ) -> tuple[QueryPlan | None, AgentResult]:
        """Returns (plan, agent_result). On error, plan is None and
        agent_result.error is populated."""
        from google.genai import types

        user_prompt = _build_user_prompt(question, schema_card)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            response_mime_type="application/json",
            response_schema=QueryPlan,
        )

        start = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=config,
            )
        except Exception as e:
            return None, AgentResult(
                agent=self.name,
                output={},
                latency_ms=int((time.perf_counter() - start) * 1000),
                error=f"{type(e).__name__}: {e}",
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        usage = response.usage_metadata
        tokens_in = (usage.prompt_token_count or 0) if usage else 0
        tokens_out = (usage.candidates_token_count or 0) if usage else 0
        text = (response.text or "").strip()

        # Controlled generation should give us valid JSON; we still parse
        # defensively because empty/truncated responses are possible.
        try:
            payload = json.loads(text) if text else {}
            plan = QueryPlan.model_validate(payload)
        except Exception as e:
            return None, AgentResult(
                agent=self.name,
                output={"raw_text": text[:1000]},
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                cost_usd=_estimate_cost(tokens_in, tokens_out),
                error=f"plan parse failed: {type(e).__name__}: {e}",
            )

        return plan, AgentResult(
            agent=self.name,
            output=plan.model_dump(),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=_estimate_cost(tokens_in, tokens_out),
            error=None,
        )

    def plan_sync(
        self, question: str, schema_card: SchemaCard
    ) -> tuple[QueryPlan | None, AgentResult]:
        """Convenience sync wrapper for scripts and notebooks."""
        return asyncio.run(self.plan(question, schema_card))
