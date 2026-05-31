"""CLI for the baseline runner.

Usage:
    # M1.3 full-context baseline (Gemini 2.5 Pro, whole spreadsheet)
    uv run python scripts/run_baseline.py
    # equivalent:
    uv run python scripts/run_baseline.py --strategy fullcontext

    # M1.4 naive RAG baseline (chunked retrieval + top-K)
    uv run python scripts/run_baseline.py --strategy naive_rag

    # Dry run, just 10 questions
    uv run python scripts/run_baseline.py --strategy naive_rag --limit 10

    # Regenerate report from existing results without re-running
    uv run python scripts/run_baseline.py --strategy naive_rag --report-only

    # Re-grade existing results against current verifier (no LLM calls)
    uv run python scripts/run_baseline.py --strategy naive_rag --rescore
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

# Make src/ imports work without installing the package
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from finsheet.baselines.naive_rag_solver import NaiveRagSolver  # noqa: E402
from finsheet.baselines.report import write_report  # noqa: E402
from finsheet.baselines.runner import run_baseline  # noqa: E402
from finsheet.baselines.solver import FullContextSolver  # noqa: E402


def make_progress_bar():
    """Lightweight progress bar; falls back to print if tqdm unavailable."""
    try:
        from tqdm import tqdm

        bar = {"obj": None}

        def cb(done: int, total: int):
            if bar["obj"] is None:
                bar["obj"] = tqdm(total=total, desc="baseline", unit="q")
            bar["obj"].update(1)

        return cb
    except ImportError:

        def cb(done: int, total: int):
            if done % 10 == 0 or done == total:
                print(f"  [{done}/{total}]")

        return cb


# Strategy → (default results file, solver factory).
def _strategy_config(strategy: str, client, model: str):
    if strategy == "fullcontext":
        return (
            "bench/data/results/baseline_fullcontext.jsonl",
            FullContextSolver(client, model=model),
        )
    if strategy == "naive_rag":
        return (
            "bench/data/results/baseline_naive_rag.jsonl",
            NaiveRagSolver(client, chat_model=model),
        )
    raise ValueError(f"Unknown strategy: {strategy}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--strategy",
        choices=["fullcontext", "naive_rag"],
        default="fullcontext",
        help="M1.3 = fullcontext (default), M1.4 = naive_rag",
    )
    p.add_argument(
        "--bench-dir", default="bench/data", help="Directory with files/ and ground_truth.jsonl"
    )
    p.add_argument(
        "--results-path",
        default=None,
        help="Override default results path (defaults to strategy-specific path).",
    )
    p.add_argument("--report-path", default="docs/eval-report.md")
    p.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max in-flight LLM calls. Default 5 is safe for GA quotas; "
        "lower to 2-3 if you still hit 429s.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N pending questions (dry run).",
    )
    p.add_argument(
        "--report-only",
        action="store_true",
        help="Skip running; regenerate report from existing results.",
    )
    p.add_argument(
        "--rescore",
        action="store_true",
        help="Re-grade existing results against current verifier "
        "(no LLM calls), then regenerate report.",
    )
    p.add_argument("--model", default=os.environ.get("GEMINI_PRO_MODEL", "gemini-2.5-pro"))
    args = p.parse_args()

    # Determine results path: --results-path override > strategy default
    default_results_per_strategy = {
        "fullcontext": "bench/data/results/baseline_fullcontext.jsonl",
        "naive_rag": "bench/data/results/baseline_naive_rag.jsonl",
    }
    results_path_str = args.results_path or default_results_per_strategy[args.strategy]
    results_path = Path(results_path_str)
    bench_dir = Path(args.bench_dir)
    report_path = Path(args.report_path)

    solver_name = (
        "fullcontext_gemini_2.5_pro"
        if args.strategy == "fullcontext"
        else "naive_rag_gemini_2.5_pro"
    )

    if args.rescore:
        from finsheet.baselines.rescore import rescore_file

        if not results_path.exists():
            print(f"✗ No results file at {results_path}. Run without --rescore first.")
            return 1
        summary = rescore_file(results_path)
        print(f"✓ Re-graded {summary['regraded']} records")
        print(f"  Flipped to correct: {summary['flipped_to_correct']}")
        print(f"  Flipped to incorrect: {summary['flipped_to_incorrect']}")
        print(f"  Unchanged: {summary['unchanged']}")
        print(f"  Skipped (solver error): {summary['skipped_error']}")
        print(f"  Skipped (empty response): {summary['skipped_empty']}")
        aggregates = write_report(results_path, report_path, solver_name=solver_name)
        print(f"\n✓ Report written to {report_path}")
        print(
            f"  New overall accuracy: {aggregates['overall']['accuracy'] * 100:.1f}% "
            f"({aggregates['overall']['correct']}/{aggregates['overall']['total']})"
        )
        return 0

    if args.report_only:
        if not results_path.exists():
            print(f"✗ No results file at {results_path}. Run without --report-only first.")
            return 1
        aggregates = write_report(results_path, report_path, solver_name=solver_name)
        print(f"✓ Report written to {report_path}")
        print(
            f"  Overall accuracy: {aggregates['overall']['accuracy'] * 100:.1f}% "
            f"({aggregates['overall']['correct']}/{aggregates['overall']['total']})"
        )
        print(f"  Total cost: ${aggregates['cost']['total_usd']:.2f}")
        return 0

    # Initialize Gemini client
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

    client = genai.Client(vertexai=True, project=project_id, location=region)
    _, solver = _strategy_config(args.strategy, client, args.model)

    print(f"Running baseline: {solver.name}  [strategy={args.strategy}]")
    print(f"  Bench: {bench_dir}")
    print(f"  Results: {results_path}")
    print(f"  Concurrency: {args.concurrency}")
    if args.limit:
        print(f"  Limit: {args.limit} (dry run)")
    print()

    summary = asyncio.run(
        run_baseline(
            solver=solver,
            bench_data_dir=bench_dir,
            results_path=results_path,
            concurrency=args.concurrency,
            progress_cb=make_progress_bar(),
            limit=args.limit,
        )
    )

    print()
    print("✓ Run complete.")
    print(f"  Accuracy: {summary['accuracy'] * 100:.1f}% ({summary['correct']}/{summary['total']})")
    print(f"  Cost: ${summary['cost_usd']:.2f}")
    if hasattr(solver, "embedding_cost_total"):
        print(f"  (Embedding cost: ${solver.embedding_cost_total:.4f})")
    print(f"  Wall-clock: {summary.get('wall_clock_minutes', '?')} min")
    print(f"  Errors: {summary['errors']}")
    print()

    aggregates = write_report(
        results_path,
        report_path,
        solver_name=solver.name,
        wall_clock_minutes=summary.get("wall_clock_minutes"),
    )
    print(f"✓ Report: {report_path}")
    print(f"✓ Aggregates JSON: {report_path.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
