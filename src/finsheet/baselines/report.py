"""
Aggregate scoring + markdown report generator.

Consumes a results JSONL (produced by runner.py) and emits a structured
eval report covering:
  - Headline accuracy + comparison to FinSheet-Bench paper
  - Per-file accuracy with hardest-tier callout
  - Per-category accuracy
  - Per-complexity accuracy
  - Per-question-template accuracy
  - Failure-mode samples
  - Cost / latency / token stats
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Stat:
    correct: int
    total: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def __iadd__(self, other_correct: bool):
        self.total += 1
        if other_correct:
            self.correct += 1
        return self


# FinSheet-Bench paper reference numbers.
# 3.1 Pro: 82.4% overall (best in paper).
# 2.5 Pro: ~75% overall (estimated from paper's model breakdown; GA model,
#          what most production deployments use today).
# 48.6% is the average across ALL models tested on synthetic4_A (hardest file).
PAPER_31_PRO_OVERALL = 0.824
PAPER_25_PRO_OVERALL = 0.75  # approximate; check paper Table 4 for exact figure
PAPER_HARD_AVG = 0.486


def _paper_overall_for(solver_name: str) -> tuple[float, str]:
    """Return the appropriate paper reference number + label for the solver."""
    if "2.5" in solver_name or "25" in solver_name:
        return PAPER_25_PRO_OVERALL, "Gemini 2.5 Pro (paper, approx)"
    return PAPER_31_PRO_OVERALL, "Gemini 3.1 Pro (paper)"


def load_results(results_path: Path) -> list[dict]:
    out = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def aggregate(results: list[dict]) -> dict:
    """Compute the full set of aggregate statistics from results."""
    by_file: dict[str, Stat] = defaultdict(lambda: Stat(0, 0))
    by_category: dict[str, Stat] = defaultdict(lambda: Stat(0, 0))
    by_complexity: dict[str, Stat] = defaultdict(lambda: Stat(0, 0))
    by_template: dict[int, Stat] = defaultdict(lambda: Stat(0, 0))
    by_version: dict[str, Stat] = defaultdict(lambda: Stat(0, 0))
    by_tier: dict[int, int] = defaultdict(int)

    overall_correct = 0
    overall_total = 0
    total_cost = 0.0
    total_tokens_in = 0
    total_tokens_out = 0
    latencies = []
    error_count = 0

    # Hardest tier from FinSheet-Bench is synthetic4_A (152 companies × 8 funds).
    hard_correct = 0
    hard_total = 0

    for r in results:
        overall_total += 1
        if r["verdict_correct"]:
            overall_correct += 1

        file_key = f"{r['file_id']}_{r['version']}"
        by_file[file_key] += r["verdict_correct"]
        by_category[r["category"]] += r["verdict_correct"]
        by_complexity[r["complexity"]] += r["verdict_correct"]
        by_template[r["template_id"]] += r["verdict_correct"]
        by_version[r["version"]] += r["verdict_correct"]
        by_tier[r["verdict_tier"]] += 1

        if file_key == "synthetic4_A":
            hard_total += 1
            if r["verdict_correct"]:
                hard_correct += 1

        total_cost += r.get("cost_usd", 0.0) or 0.0
        total_tokens_in += r.get("tokens_in", 0) or 0
        total_tokens_out += r.get("tokens_out", 0) or 0
        if r.get("latency_ms"):
            latencies.append(r["latency_ms"])
        if r.get("error"):
            error_count += 1

    overall_acc = overall_correct / overall_total if overall_total else 0.0
    hard_acc = hard_correct / hard_total if hard_total else 0.0

    return {
        "overall": {
            "correct": overall_correct,
            "total": overall_total,
            "accuracy": overall_acc,
        },
        "hard_tier_synthetic4_A": {
            "correct": hard_correct,
            "total": hard_total,
            "accuracy": hard_acc,
        },
        "by_file": {
            k: {"correct": s.correct, "total": s.total, "accuracy": s.accuracy}
            for k, s in sorted(by_file.items())
        },
        "by_category": {
            k: {"correct": s.correct, "total": s.total, "accuracy": s.accuracy}
            for k, s in sorted(by_category.items())
        },
        "by_complexity": {
            k: {"correct": s.correct, "total": s.total, "accuracy": s.accuracy}
            for k, s in sorted(
                by_complexity.items(),
                key=lambda x: (
                    ["Low", "Medium", "High", "Very High"].index(x[0])
                    if x[0] in ["Low", "Medium", "High", "Very High"]
                    else 99
                ),
            )
        },
        "by_template": {
            k: {"correct": s.correct, "total": s.total, "accuracy": s.accuracy}
            for k, s in sorted(by_template.items())
        },
        "by_version": {
            k: {"correct": s.correct, "total": s.total, "accuracy": s.accuracy}
            for k, s in sorted(by_version.items())
        },
        "verdict_tier_distribution": dict(by_tier),
        "cost": {
            "total_usd": round(total_cost, 4),
            "tokens_in_total": total_tokens_in,
            "tokens_out_total": total_tokens_out,
            "mean_tokens_in": round(total_tokens_in / overall_total) if overall_total else 0,
            "mean_tokens_out": round(total_tokens_out / overall_total) if overall_total else 0,
        },
        "latency": {
            "mean_ms": round(statistics.mean(latencies)) if latencies else 0,
            "p50_ms": round(statistics.median(latencies)) if latencies else 0,
            "p95_ms": round(statistics.quantiles(latencies, n=20)[-1])
            if len(latencies) >= 20
            else 0,
            "max_ms": max(latencies) if latencies else 0,
        },
        "errors": error_count,
    }


def _sample_failures(results: list[dict], n: int = 10, seed: int = 42) -> list[dict]:
    """Return n random failure samples for the failure-mode section."""
    failures = [r for r in results if not r["verdict_correct"] and not r.get("error")]
    if not failures:
        return []
    rng = random.Random(seed)
    return rng.sample(failures, min(n, len(failures)))


def _format_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_report(
    results: list[dict],
    aggregates: dict,
    solver_name: str,
    wall_clock_minutes: float | None = None,
) -> str:
    """Render the markdown eval report."""
    lines: list[str] = []
    lines.append(f"# Eval Report — {solver_name}")
    lines.append("")
    lines.append(
        "Auto-generated by `src.finsheet.baselines.report`. Regenerate with `uv run python scripts/run_baseline.py --report-only`."
    )
    lines.append("")

    # Headline
    overall = aggregates["overall"]
    hard = aggregates["hard_tier_synthetic4_A"]
    paper_overall, paper_label = _paper_overall_for(solver_name)
    lines.append("## Headline")
    lines.append("")
    lines.append(
        f"- **Overall accuracy: {_format_pct(overall['accuracy'])}** ({overall['correct']} / {overall['total']})"
    )
    lines.append(f"  - FinSheet-Bench paper, {paper_label}: **{_format_pct(paper_overall)}**")
    delta = overall["accuracy"] - paper_overall
    lines.append(f"  - Δ vs paper: **{delta * 100:+.1f}pp**")
    lines.append("")
    lines.append(
        f"- **Hard tier (synthetic4_A): {_format_pct(hard['accuracy'])}** ({hard['correct']} / {hard['total']})"
    )
    lines.append(
        f"  - FinSheet-Bench paper avg across all models on synthetic4_A: **{_format_pct(PAPER_HARD_AVG)}**"
    )
    delta_hard = hard["accuracy"] - PAPER_HARD_AVG
    lines.append(f"  - Δ vs paper avg: **{delta_hard * 100:+.1f}pp**")
    lines.append("")

    # Cost + latency
    cost = aggregates["cost"]
    latency = aggregates["latency"]
    lines.append("## Cost & latency")
    lines.append("")
    lines.append(f"- Total cost: **${cost['total_usd']:.2f}**")
    lines.append(
        f"- Total tokens: {cost['tokens_in_total']:,} in / {cost['tokens_out_total']:,} out"
    )
    lines.append(
        f"- Per-question avg: {cost['mean_tokens_in']:,} in / {cost['mean_tokens_out']:,} out"
    )
    lines.append(
        f"- Latency: mean {latency['mean_ms']}ms, p50 {latency['p50_ms']}ms, p95 {latency['p95_ms']}ms, max {latency['max_ms']}ms"
    )
    if wall_clock_minutes is not None:
        lines.append(f"- Wall-clock: **{wall_clock_minutes:.1f} min**")
    lines.append(f"- Errors: {aggregates['errors']}")
    lines.append("")

    # By complexity (matches paper's framing)
    lines.append("## Accuracy by complexity")
    lines.append("")
    lines.append("| Complexity | Accuracy | N |")
    lines.append("|---|---|---|")
    for k, v in aggregates["by_complexity"].items():
        lines.append(f"| {k} | {_format_pct(v['accuracy'])} | {v['total']} |")
    lines.append("")

    # By category (the 7 question categories)
    lines.append("## Accuracy by category")
    lines.append("")
    lines.append("| Category | Accuracy | N |")
    lines.append("|---|---|---|")
    by_cat = sorted(aggregates["by_category"].items(), key=lambda x: -x[1]["accuracy"])
    for k, v in by_cat:
        lines.append(f"| {k} | {_format_pct(v['accuracy'])} | {v['total']} |")
    lines.append("")

    # By file
    lines.append("## Accuracy by file")
    lines.append("")
    lines.append("| File | Accuracy | N |")
    lines.append("|---|---|---|")
    for k, v in aggregates["by_file"].items():
        marker = " ←  hardest" if k == "synthetic4_A" else ""
        lines.append(f"| {k}{marker} | {_format_pct(v['accuracy'])} | {v['total']} |")
    lines.append("")

    # By question template
    lines.append("## Accuracy by question template")
    lines.append("")
    lines.append("| Q# | Accuracy | N |")
    lines.append("|---|---|---|")
    for k, v in aggregates["by_template"].items():
        lines.append(f"| Q{k} | {_format_pct(v['accuracy'])} | {v['total']} |")
    lines.append("")

    # Verifier tier distribution (useful: high Tier 3 share = many uncertain answers)
    lines.append("## Verifier tier distribution")
    lines.append("")
    lines.append("| Tier | Count | Notes |")
    lines.append("|---|---|---|")
    tier_notes = {
        0: "(solver error)",
        1: "(exact match, high confidence)",
        2: "(fuzzy match, moderate confidence)",
        3: "(LLM adjudication or unresolved)",
    }
    for tier, count in sorted(aggregates["verdict_tier_distribution"].items()):
        lines.append(f"| {tier} | {count} | {tier_notes.get(tier, '')} |")
    lines.append("")

    # Failure samples
    failures = _sample_failures(results, n=10)
    if failures:
        lines.append("## Failure samples (random 10)")
        lines.append("")
        for i, r in enumerate(failures, 1):
            lines.append(f"### Failure {i} — {r['file_id']}_{r['version']} Q{r['template_id']}")
            lines.append("")
            lines.append(f"- **Question:** {r['question']}")
            lines.append(f"- **Ground truth:** `{r['ground_truth']!r}`")
            lines.append(f"- **Response:** `{r['raw_response'][:300]}`")
            lines.append(
                f"- **Verdict:** Tier {r['verdict_tier']}, confidence {r['verdict_confidence']:.2f}"
            )
            lines.append(f"- **Reason:** {r['verdict_explanation']}")
            lines.append("")

    return "\n".join(lines)


def write_report(
    results_path: Path,
    report_path: Path,
    solver_name: str,
    wall_clock_minutes: float | None = None,
) -> dict:
    results = load_results(results_path)
    aggregates = aggregate(results)
    markdown = render_report(results, aggregates, solver_name, wall_clock_minutes)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown, encoding="utf-8")
    # Also persist aggregates JSON next to the report for downstream tooling
    json_path = report_path.with_suffix(".json")
    json_path.write_text(json.dumps(aggregates, indent=2), encoding="utf-8")
    return aggregates
