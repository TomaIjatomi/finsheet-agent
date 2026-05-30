"""
Ground-truth generation.

For each (template × version × question_template × parameter_sample),
compute the deterministic answer from the canonical DataFrame.
Output: one JSONL file per (template × version) with all questions for that file.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .questions import TEMPLATES as QUESTION_TEMPLATES
from .questions import render_prompt, sample_parameters


def _serialize(value):
    """Convert ground-truth values to JSON-friendly types."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, (int, float)):
        return value
    return str(value)


def generate_ground_truth(canonical_df: pd.DataFrame, file_id: str, version: str,
                          sample_size: int = 3, seed: int = 42) -> list[dict]:
    """Compute all GT questions for one file. Returns list of question records."""
    records = []
    qnum = 0
    for tmpl in QUESTION_TEMPLATES:
        param_sets = sample_parameters(tmpl, canonical_df, sample_size, seed)
        for params in param_sets:
            qnum += 1
            answer = tmpl.compute_fn(canonical_df, params)
            if answer is None:
                # Skip questions that can't be answered on this file
                continue
            records.append({
                "qid": qnum,
                "template_id": tmpl.qid,
                "file_id": file_id,
                "version": version,
                "question": render_prompt(tmpl, params),
                "category": tmpl.category,
                "complexity": tmpl.complexity,
                "answer_type": tmpl.answer_type,
                "parameters": params,
                "ground_truth": _serialize(answer),
            })
    return records


def write_ground_truth(records: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
