"""End-to-end demo of the M2.2 agents.

Usage:
    uv run python scripts/run_agent_plan.py \\
        bench/data/files/synthetic4_A.xlsx \\
        "What is the total unrealized capital per fund?"

Prints the SchemaCard, the QueryPlan, and timing/cost. No execution
happens — the Computation Agent in M2.3 turns the plan into actual
pandas code.
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

from finsheet.agents import DecompositionAgent, SchemaAgent  # noqa: E402


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("file_path", help="Path to an xlsx file")
    p.add_argument("question", help="Natural-language question")
    p.add_argument("--sheet", default=None)
    p.add_argument("--model", default=os.environ.get("GEMINI_PRO_MODEL", "gemini-2.5-pro"))
    args = p.parse_args()

    project_id = os.environ.get("GCP_PROJECT_ID")
    region = os.environ.get("GCP_REGION", "global")
    if not project_id:
        print("✗ GCP_PROJECT_ID not set. Configure .env first.")
        return 1
    try:
        from google import genai
    except ImportError:
        print("✗ google-genai not installed. Run: uv sync --extra dev")
        return 1

    print(f"=== Schema Agent (deterministic) on {args.file_path} ===")
    card = SchemaAgent().profile(args.file_path, sheet=args.sheet)
    print(f"  sheet: {card.sheet_name}  ({card.n_rows} rows × {card.n_cols} cols)")
    print(f"  header_row: {card.header_row}")
    print(f"  data_range: {card.data_range}")
    print(f"  fund_layout: {card.fund_layout}")
    print(f"  columns ({len(card.columns)}):")
    for c in card.columns:
        print(f"    {c.col_letter}: {c.name} ({c.dtype})")
    print(f"  funds ({len(card.funds)}):")
    for f in card.funds:
        print(f"    {f.fund}: rows {f.start_row}-{f.end_row} ({f.n_companies} cos)")
    if card.average_rows:
        print(f"  average_rows: {card.average_rows}")
    if card.structural_notes:
        print("  structural_notes:")
        for note in card.structural_notes:
            print(f"    - {note}")

    print(f"\n=== Decomposition Agent ({args.model}) ===")
    client = genai.Client(vertexai=True, project=project_id, location=region)
    agent = DecompositionAgent(client=client, model=args.model)
    plan, agent_result = await agent.plan(args.question, card)

    if plan is None:
        print(f"✗ Decomposition failed: {agent_result.error}")
        return 1

    print(f"  Question: {args.question}")
    print(f"  Interpretation: {plan.interpretation}")
    print(f"  Expected answer type: {plan.expected_answer_type}")
    print(f"  Expected output shape: {plan.expected_output_shape}")
    print(f"  Needed columns: {plan.needed_columns}")
    print("  Subgoals:")
    for sg in plan.subgoals:
        print(f"    {sg.step_number}. [{sg.operation}] {sg.description}")
        if sg.pandas_hint:
            print(f"       pandas hint: {sg.pandas_hint}")
    if plan.notes:
        print(f"  Notes: {plan.notes}")
    print()
    print(f"  Tokens in:  {agent_result.tokens_in}")
    print(f"  Tokens out: {agent_result.tokens_out}")
    print(f"  Latency:    {agent_result.latency_ms} ms")
    print(f"  Cost:       ${agent_result.cost_usd:.5f}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
