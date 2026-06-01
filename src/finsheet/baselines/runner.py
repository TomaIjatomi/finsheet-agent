"""
Async baseline runner.

Loads ground_truth.jsonl, iterates each (file, question) pair through
the configured Solver, captures raw response + verdict + metadata,
persists incrementally to results JSONL.

Designed to survive crashes mid-run: on restart, reads existing results
and skips qids that already have responses with no error.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from bench.verifier import Verdict, verify

from .solver import Solver, SolveResult


@dataclass
class QuestionRecord:
    qid: int
    template_id: int
    file_id: str
    version: str
    question: str
    category: str
    complexity: str
    answer_type: str
    parameters: dict
    ground_truth: object


@dataclass
class ResultRecord:
    # Identity
    qid: int
    template_id: int
    file_id: str
    version: str
    question: str
    category: str
    complexity: str
    answer_type: str
    ground_truth: object
    # Output
    raw_response: str
    verdict_correct: bool
    verdict_tier: int
    verdict_confidence: float
    verdict_explanation: str
    # Metadata
    solver: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    error: str | None
    timestamp: float


def _resolve_xlsx_path(record: QuestionRecord, files_dir: Path) -> Path:
    return files_dir / f"{record.file_id}_{record.version}.xlsx"


def _load_questions(gt_path: Path) -> list[QuestionRecord]:
    records = []
    with open(gt_path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            records.append(
                QuestionRecord(
                    qid=d["qid"],
                    template_id=d["template_id"],
                    file_id=d["file_id"],
                    version=d["version"],
                    question=d["question"],
                    category=d["category"],
                    complexity=d["complexity"],
                    answer_type=d["answer_type"],
                    parameters=d["parameters"],
                    ground_truth=d["ground_truth"],
                )
            )
    return records


def _load_existing(results_path: Path) -> dict[tuple[str, str, int], dict]:
    """Return mapping of (file_id, version, qid) -> existing result dict."""
    if not results_path.exists():
        return {}
    out: dict[tuple[str, str, int], dict] = {}
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
                # Only treat as 'done' if there was no error
                if d.get("error") is None:
                    out[(d["file_id"], d["version"], d["qid"])] = d
            except json.JSONDecodeError:
                continue
    return out


async def _bounded_solve(
    semaphore: asyncio.Semaphore,
    solver: Solver,
    record: QuestionRecord,
    xlsx_path: Path,
    max_retries: int = 5,
) -> SolveResult:
    """Run one solver call with concurrency cap and exponential backoff.

    Backoff schedule: 5 * 2^attempt + jitter — peaks at ~80s after 5 retries.
    Calibrated for GCP 429 RESOURCE_EXHAUSTED recovery (quota windows are
    typically ~30-60s on Vertex AI).
    """
    async with semaphore:
        last_exc: str | None = None
        for attempt in range(max_retries):
            result = await solver.solve(xlsx_path, record.question, record.answer_type)
            if result.error is None:
                return result
            last_exc = result.error
            # Retry on transient errors (429, 500, timeout)
            transient = any(
                s in last_exc.lower()
                for s in (
                    "429",
                    "500",
                    "503",
                    "timeout",
                    "connection",
                    "resource_exhausted",
                    "deadline",
                )
            )
            if not transient:
                return result
            backoff = (5 * (2**attempt)) + random.uniform(0, 3)
            await asyncio.sleep(backoff)
        # Exhausted retries
        return SolveResult(
            answer_text="",
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
            cost_usd=0.0,
            error=f"max_retries_exceeded: {last_exc}",
        )


def _score(record: QuestionRecord, raw_response: str) -> Verdict:
    """Verify the response against ground truth using the cascading verifier."""
    return verify(raw_response, record.ground_truth, record.answer_type)


def _to_result(
    record: QuestionRecord,
    solve_result: SolveResult,
    verdict: Verdict,
    solver_name: str,
) -> ResultRecord:
    return ResultRecord(
        qid=record.qid,
        template_id=record.template_id,
        file_id=record.file_id,
        version=record.version,
        question=record.question,
        category=record.category,
        complexity=record.complexity,
        answer_type=record.answer_type,
        ground_truth=record.ground_truth,
        raw_response=solve_result.answer_text,
        verdict_correct=verdict.correct,
        verdict_tier=verdict.tier,
        verdict_confidence=verdict.confidence,
        verdict_explanation=verdict.explanation,
        solver=solver_name,
        tokens_in=solve_result.tokens_in,
        tokens_out=solve_result.tokens_out,
        latency_ms=solve_result.latency_ms,
        cost_usd=solve_result.cost_usd,
        error=solve_result.error,
        timestamp=time.time(),
    )


async def run_baseline(
    solver: Solver,
    bench_data_dir: Path,
    results_path: Path,
    concurrency: int = 10,
    progress_cb: Callable[[int, int], None] | None = None,
    limit: int | None = None,
    files: set[str] | None = None,
) -> dict:
    """Run a baseline end-to-end.

    Args:
        solver: implements the Solver protocol (FullContextSolver etc.)
        bench_data_dir: path to bench/data/ (must contain files/ and ground_truth.jsonl)
        results_path: JSONL output path. Resumable — existing results are kept and skipped.
        concurrency: max in-flight calls (default 10; tune per quota).
        progress_cb: optional callback(done, total) for UI/progress bar.
        limit: if set, only run the first N pending questions (for dry runs).
        files: if set, only run questions for these file_id_version strings
            (e.g. {"synthetic4_A", "synthetic1_A"}). For stratified partial evals.

    Returns:
        Summary dict with counts, accuracy, total cost, wall-clock time.
    """
    gt_path = bench_data_dir / "ground_truth.jsonl"
    files_dir = bench_data_dir / "files"
    results_path.parent.mkdir(parents=True, exist_ok=True)

    all_questions = _load_questions(gt_path)
    if files is not None:
        all_questions = [q for q in all_questions if f"{q.file_id}_{q.version}" in files]
    existing = _load_existing(results_path)
    pending = [q for q in all_questions if (q.file_id, q.version, q.qid) not in existing]
    if limit is not None:
        pending = pending[:limit]

    total = len(all_questions)
    already_done = len(existing)
    todo = len(pending)

    print(f"Total questions: {total}")
    print(f"Already complete: {already_done}")
    print(f"To process: {todo}")
    if todo == 0:
        print("Nothing to do.")
        return _summarize(results_path)

    semaphore = asyncio.Semaphore(concurrency)
    start_time = time.time()
    completed_count = 0
    completed_lock = asyncio.Lock()
    write_lock = asyncio.Lock()

    async def process_one(record: QuestionRecord):
        nonlocal completed_count
        xlsx_path = _resolve_xlsx_path(record, files_dir)
        solve_result = await _bounded_solve(semaphore, solver, record, xlsx_path)
        if solve_result.error is None:
            verdict = _score(record, solve_result.answer_text)
        else:
            verdict = Verdict(
                correct=False,
                tier=0,
                confidence=0.0,
                extracted_value=None,
                explanation=f"Solver error: {solve_result.error}",
            )
        result = _to_result(record, solve_result, verdict, solver.name)
        async with write_lock:
            with open(results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(result), default=str) + "\n")
        async with completed_lock:
            completed_count += 1
            if progress_cb:
                progress_cb(completed_count, todo)

    tasks = [asyncio.create_task(process_one(q)) for q in pending]
    await asyncio.gather(*tasks)

    elapsed = time.time() - start_time
    summary = _summarize(results_path)
    summary["wall_clock_seconds"] = round(elapsed, 1)
    summary["wall_clock_minutes"] = round(elapsed / 60, 2)
    return summary


def _summarize(results_path: Path) -> dict:
    """Compute aggregate stats from the results JSONL."""
    if not results_path.exists():
        return {"total": 0, "correct": 0, "accuracy": 0.0}
    correct = 0
    total = 0
    cost_total = 0.0
    tokens_in_total = 0
    tokens_out_total = 0
    errors = 0
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            if d.get("verdict_correct"):
                correct += 1
            if d.get("error"):
                errors += 1
            cost_total += d.get("cost_usd", 0.0)
            tokens_in_total += d.get("tokens_in", 0)
            tokens_out_total += d.get("tokens_out", 0)
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "errors": errors,
        "cost_usd": round(cost_total, 4),
        "tokens_in_total": tokens_in_total,
        "tokens_out_total": tokens_out_total,
    }
