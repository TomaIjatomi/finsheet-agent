"""End-to-end demo of the full M2.3 pipeline.

Usage:
    uv run python scripts/run_question_end_to_end.py \\
        bench/data/files/synthetic4_A.xlsx \\
        "What is the total unrealized capital per fund?"

Runs:
  1. SchemaAgent (deterministic)         → SchemaCard
  2. DecompositionAgent (Gemini 2.5 Pro) → QueryPlan
  3. ComputationAgent (Gemini 2.5 Pro)   → FactSheet + final answer
     - One Gemini call per subgoal
     - execute_python via MCP (DockerSandbox by default)

Prints schema, plan, fact sheet (with the code that produced each value),
and the final answer.

Use --sandbox=local_unsafe to bypass Docker for quick iteration on the
prompts. Production always uses docker.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from finsheet.agents import (  # noqa: E402
    ComputationAgent,
    DecompositionAgent,
    SchemaAgent,
)
from finsheet.mcp.sandbox import make_sandbox  # noqa: E402
from finsheet.mcp.server import tool_execute_python  # noqa: E402


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("file_path")
    p.add_argument("question")
    p.add_argument("--sheet", default=None)
    p.add_argument("--model", default=os.environ.get("GEMINI_PRO_MODEL", "gemini-2.5-pro"))
    p.add_argument(
        "--sandbox",
        choices=["docker", "local_unsafe"],
        default="docker",
        help="Code-execution sandbox. 'docker' is production; 'local_unsafe' is "
        "fast iteration only — refuses to construct without --allow-unsafe.",
    )
    p.add_argument("--max-retries", type=int, default=2)
    args = p.parse_args()

    project_id = os.environ.get("GCP_PROJECT_ID")
    region = os.environ.get("GCP_REGION", "global")
    if not project_id:
        print("✗ GCP_PROJECT_ID not set.")
        return 1
    try:
        from google import genai
    except ImportError:
        print("✗ google-genai not installed: uv sync --extra dev")
        return 1

    # Stage 1 — Schema (deterministic)
    print(f"=== [1/3] Schema Agent on {args.file_path} ===")
    card = SchemaAgent().profile(args.file_path, sheet=args.sheet)
    print(f"  sheet: {card.sheet_name}  ({card.n_rows}×{card.n_cols})")
    print(f"  data_range: {card.data_range}")
    print(
        f"  fund_layout: {card.fund_layout}  ({len(card.funds)} funds, "
        f"{sum(f.n_companies for f in card.funds)} companies)"
    )

    # Stage 2 — Decomposition (LLM, controlled gen)
    print(f"\n=== [2/3] Decomposition Agent ({args.model}) ===")
    client = genai.Client(vertexai=True, project=project_id, location=region)
    decomp = DecompositionAgent(client=client, model=args.model)
    plan, decomp_result = await decomp.plan(args.question, card)
    if plan is None:
        print(f"✗ Decomposition failed: {decomp_result.error}")
        return 1
    print(f"  interpretation: {plan.interpretation}")
    print(f"  expected_answer_type: {plan.expected_answer_type}")
    print(f"  subgoals ({len(plan.subgoals)}):")
    for sg in plan.subgoals:
        print(f"    {sg.step_number}. [{sg.operation}] {sg.description}")
    print(f"  decomp cost: ${decomp_result.cost_usd:.5f}")

    # Stage 3 — Computation (LLM codegen + sandboxed execution per subgoal)
    print(f"\n=== [3/3] Computation Agent ({args.model}, sandbox={args.sandbox}) ===")
    if args.sandbox == "docker":
        sb = make_sandbox(prefer="docker")
    else:
        print("⚠ Using LocalSandbox — NEVER ship this with untrusted prompts.")
        sb = make_sandbox(prefer="local_unsafe", allow_unsafe=True)

    def exec_python_fn(file_path, code, named_ranges, timeout_s=30):
        return tool_execute_python(
            file_path=file_path,
            code=code,
            named_ranges=named_ranges,
            sandbox=sb,
            timeout_s=timeout_s,
        )

    comp = ComputationAgent(
        client=client,
        execute_python_fn=exec_python_fn,
        model=args.model,
        max_retries=args.max_retries,
    )
    result = await comp.execute(plan, card)

    # Print the Fact Sheet
    print(
        f"  subgoals attempted: {result.n_subgoals_attempted}, "
        f"succeeded: {result.n_subgoals_succeeded}, retries: {result.n_retries}"
    )
    print("  Fact Sheet:")
    for entry in result.fact_sheet.entries:
        status = "✓" if entry.error is None else "✗"
        val_repr = repr(entry.value)
        if len(val_repr) > 120:
            val_repr = val_repr[:120] + "..."
        print(f"    {status} Step {entry.subgoal_step}: {entry.subgoal_description}")
        print(f"        value: {val_repr}")
        if entry.error:
            print(f"        error: {entry.error}")
        if entry.attempts > 1:
            print(f"        attempts: {entry.attempts}")

    print(f"\n  computation cost: ${result.total_cost_usd:.5f}")
    print(
        f"  computation latency: {result.total_latency_ms} ms "
        f"({result.total_latency_ms / 1000:.1f} s)"
    )

    print(f"\n=== FINAL ANSWER ({plan.expected_answer_type}) ===")
    if result.final_answer is None:
        print("(no answer — see Fact Sheet for failures)")
        return 1
    print(f"  {result.final_answer}")

    total_cost = decomp_result.cost_usd + result.total_cost_usd
    print(f"\nTotal cost this question: ${total_cost:.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
