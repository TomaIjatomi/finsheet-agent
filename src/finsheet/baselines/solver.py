"""
Solver protocol and the full-context Gemini 3.1 Pro baseline implementation.

The Solver abstraction lets the runner stay model-agnostic:
  - FullContextSolver (M1.3): pumps the whole spreadsheet as context
  - NaiveRagSolver (M1.4): chunks + retrieves before prompting
  - AgentSolver (M2.*): orchestrates the multi-agent pipeline

All solvers expose the same `solve()` async signature so the runner
treats them uniformly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .serializer import serialize_xlsx


@dataclass
class SolveResult:
    """One solver invocation's structured output."""

    answer_text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    error: str | None = None


class Solver(Protocol):
    """All baseline strategies and agent pipelines implement this."""

    name: str

    async def solve(
        self,
        xlsx_path: Path,
        question: str,
        answer_type: str,
    ) -> SolveResult: ...


# Gemini 2.5 Pro pricing as of May 2026 (per million tokens, GA tier)
# Note: pricing varies slightly by region; these are conservative estimates.
GEMINI_25_PRO_INPUT_PER_MTOK = 1.25
GEMINI_25_PRO_OUTPUT_PER_MTOK = 10.00


def _estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return (
        tokens_in * GEMINI_25_PRO_INPUT_PER_MTOK / 1_000_000
        + tokens_out * GEMINI_25_PRO_OUTPUT_PER_MTOK / 1_000_000
    )


class FullContextSolver:
    """Baseline #1: serialize whole spreadsheet, prompt Gemini Pro once.

    Default model is Gemini 2.5 Pro (GA) for higher quota and production
    realism. The paper benchmarked 3.1 Pro at 82.4% overall; 2.5 Pro
    typically lands a few pp lower. State the model choice explicitly in
    your eval report — direct paper comparison requires same-model setup.
    """

    name = "fullcontext_gemini_2.5_pro"

    def __init__(
        self,
        client,
        model: str = "gemini-2.5-pro",
        max_output_tokens: int = 2048,
        temperature: float = 0.0,
    ):
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        # Cache serialized xlsx text — many questions hit the same file.
        self._serialization_cache: dict[Path, str] = {}

    def _get_serialized(self, xlsx_path: Path) -> str:
        if xlsx_path not in self._serialization_cache:
            self._serialization_cache[xlsx_path] = serialize_xlsx(xlsx_path)
        return self._serialization_cache[xlsx_path]

    async def solve(
        self,
        xlsx_path: Path,
        question: str,
        answer_type: str,
    ) -> SolveResult:
        from google.genai import types

        spreadsheet_text = self._get_serialized(xlsx_path)
        user_prompt = build_user_prompt(spreadsheet_text, question, answer_type)

        loop = asyncio.get_event_loop()
        start = loop.time()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=self._temperature,
                    max_output_tokens=self._max_output_tokens,
                ),
            )
        except Exception as e:
            latency_ms = int((loop.time() - start) * 1000)
            return SolveResult(
                answer_text="",
                tokens_in=0,
                tokens_out=0,
                latency_ms=latency_ms,
                cost_usd=0.0,
                error=f"{type(e).__name__}: {e}",
            )

        latency_ms = int((loop.time() - start) * 1000)
        usage = response.usage_metadata
        tokens_in = (usage.prompt_token_count or 0) if usage else 0
        tokens_out = (usage.candidates_token_count or 0) if usage else 0

        text = (response.text or "").strip()
        return SolveResult(
            answer_text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=_estimate_cost(tokens_in, tokens_out),
            error=None,
        )
