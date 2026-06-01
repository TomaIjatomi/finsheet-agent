"""
AgentSolver — adapter that exposes the M2.2/M2.3 agent stack via the
same `Solver` protocol the M1.3 / M1.4 baselines use. This lets the
agent stack run through the existing bench runner + verifier + report
pipeline with no changes to that code, producing apples-to-apples
comparison numbers.

Per-question flow:
  1. SchemaAgent.profile(file_path)         (deterministic, ~10ms, cached)
  2. DecompositionAgent.plan(question, card) (one Gemini call)
  3. ComputationAgent.execute(plan, card)   (1 Gemini call per subgoal +
                                             execute_python via MCP)
  4. Render final_answer as text for the existing string-based verifier.

Caching: same xlsx file gets 22 questions in the bench; we cache the
SchemaCard per file_path so we don't re-introspect 22× per file.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..baselines.solver import SolveResult
from ..mcp.sandbox import Sandbox
from ..mcp.server import tool_execute_python
from .computation import ComputationAgent
from .decomposition import DecompositionAgent
from .schema import SchemaAgent
from .types import SchemaCard


def _format_answer_text(value, answer_type: str) -> str:
    """Render the agent's final_answer in a string form the M1.3 verifier
    can parse. Matches the conventions used by FullContextSolver's prompt
    so the same verifier cascades (Tier 1/2/3) apply unchanged."""
    if value is None:
        return ""
    if answer_type == "numeric":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            # Strip trailing zeros for ints stored as floats (1121.0 → '1121')
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
        return str(value)
    if answer_type == "string":
        return str(value)
    if answer_type == "list":
        if isinstance(value, list):
            return ", ".join(str(x) for x in value)
        if isinstance(value, dict):
            return ", ".join(str(v) for v in value.values())
        return str(value)
    if answer_type == "dict":
        if isinstance(value, dict):
            return "\n".join(f"{k}: {v}" for k, v in value.items())
        return str(value)
    if answer_type == "bool":
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, (int, float)):
            return "yes" if value else "no"
        return str(value)
    if answer_type == "date":
        return str(value)
    return str(value)


class AgentSolver:
    """Pluggable Solver wrapping the full agent stack.

    Construct with a Gemini client + a Sandbox (Docker for production,
    LocalSandbox for fast tests). One instance can serve any number of
    questions; the SchemaCard cache amortises schema introspection
    across the bench's ~22 questions per file.
    """

    name = "agent_gemini_2.5_pro"

    def __init__(
        self,
        client,
        sandbox: Sandbox,
        model: str = "gemini-2.5-pro",
        max_retries: int = 2,
        sandbox_timeout_s: int = 30,
    ):
        self._sandbox = sandbox
        self._schema_agent = SchemaAgent()
        self._decomposition = DecompositionAgent(client=client, model=model)

        def _exec_python_fn(file_path, code, named_ranges, timeout_s=30):
            return tool_execute_python(
                file_path=file_path,
                code=code,
                named_ranges=named_ranges,
                sandbox=self._sandbox,
                timeout_s=timeout_s,
            )

        self._computation = ComputationAgent(
            client=client,
            execute_python_fn=_exec_python_fn,
            model=model,
            max_retries=max_retries,
            sandbox_timeout_s=sandbox_timeout_s,
        )
        self._schema_cache: dict[str, SchemaCard] = {}

    async def solve(
        self,
        xlsx_path: Path | str,
        question: str,
        answer_type: str,
    ) -> SolveResult:
        start = time.perf_counter()
        file_path = str(xlsx_path)
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost = 0.0

        # --- Stage 1: Schema (deterministic, cached per file) ---
        if file_path not in self._schema_cache:
            try:
                self._schema_cache[file_path] = self._schema_agent.profile(file_path)
            except Exception as e:
                return SolveResult(
                    answer_text="",
                    tokens_in=0,
                    tokens_out=0,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    cost_usd=0.0,
                    error=f"schema failed: {type(e).__name__}: {e}",
                )
        card = self._schema_cache[file_path]

        # --- Stage 2: Decomposition ---
        try:
            plan, decomp_result = await self._decomposition.plan(question, card)
        except Exception as e:
            return SolveResult(
                answer_text="",
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                latency_ms=int((time.perf_counter() - start) * 1000),
                cost_usd=total_cost,
                error=f"decomposition exception: {type(e).__name__}: {e}",
            )
        total_tokens_in += decomp_result.tokens_in
        total_tokens_out += decomp_result.tokens_out
        total_cost += decomp_result.cost_usd
        if plan is None:
            return SolveResult(
                answer_text="",
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                latency_ms=int((time.perf_counter() - start) * 1000),
                cost_usd=total_cost,
                error=f"decomposition failed: {decomp_result.error}",
            )

        # --- Stage 3: Computation ---
        try:
            comp_result = await self._computation.execute(plan, card)
        except Exception as e:
            return SolveResult(
                answer_text="",
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                latency_ms=int((time.perf_counter() - start) * 1000),
                cost_usd=total_cost,
                error=f"computation exception: {type(e).__name__}: {e}",
            )
        total_tokens_in += comp_result.total_tokens_in
        total_tokens_out += comp_result.total_tokens_out
        total_cost += comp_result.total_cost_usd

        # --- Stage 4: Format for the verifier ---
        answer_text = _format_answer_text(comp_result.final_answer, answer_type)

        return SolveResult(
            answer_text=answer_text,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            latency_ms=int((time.perf_counter() - start) * 1000),
            cost_usd=total_cost,
            error=comp_result.error,
        )
