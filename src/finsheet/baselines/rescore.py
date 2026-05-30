"""Re-grade existing baseline results against the current verifier.

Use when the verifier has been improved and you want to update the
verdicts on existing results without paying for another LLM run.

Reads a results JSONL produced by runner.py, re-runs verify() against
each (raw_response, ground_truth) pair, overwrites the verdict fields,
and writes the updated file in place (or to a new path).
"""

from __future__ import annotations

import json
from pathlib import Path

from bench.verifier import verify


def rescore_file(results_path: Path, output_path: Path | None = None) -> dict:
    """Re-grade a results JSONL. Returns a delta summary.

    Args:
        results_path: existing results JSONL.
        output_path: where to write the re-graded file. Defaults to in-place.

    Returns:
        dict with counts of verdicts that flipped True->False, False->True,
        and unchanged.
    """
    if output_path is None:
        output_path = results_path

    records = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    flipped_to_true = 0
    flipped_to_false = 0
    unchanged = 0
    skipped_error = 0
    skipped_empty = 0

    new_records = []
    for r in records:
        # Skip records that errored at solver time — no response to re-grade
        if r.get("error"):
            skipped_error += 1
            new_records.append(r)
            continue
        if not r.get("raw_response"):
            skipped_empty += 1
            new_records.append(r)
            continue

        old_correct = r["verdict_correct"]
        verdict = verify(
            r["raw_response"],
            r["ground_truth"],
            r["answer_type"],
        )
        new_record = dict(r)
        new_record["verdict_correct"] = verdict.correct
        new_record["verdict_tier"] = verdict.tier
        new_record["verdict_confidence"] = verdict.confidence
        new_record["verdict_explanation"] = verdict.explanation
        new_records.append(new_record)

        if old_correct == verdict.correct:
            unchanged += 1
        elif verdict.correct:
            flipped_to_true += 1
        else:
            flipped_to_false += 1

    # Write back
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for r in new_records:
            f.write(json.dumps(r, default=str) + "\n")

    return {
        "total_records": len(records),
        "regraded": flipped_to_true + flipped_to_false + unchanged,
        "flipped_to_correct": flipped_to_true,
        "flipped_to_incorrect": flipped_to_false,
        "unchanged": unchanged,
        "skipped_error": skipped_error,
        "skipped_empty": skipped_empty,
    }
