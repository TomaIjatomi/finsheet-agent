"""
Build the synthetic FinSheet bench end-to-end.

Usage:
    python -m bench.build [--out-dir bench/data]

Output:
    bench/data/files/synthetic{1..4}_{A,B,C}.xlsx        # 12 xlsx files
    bench/data/files/synthetic{1..4}_{A,B,C}.canonical.parquet  # underlying canonical DataFrames
    bench/data/ground_truth.jsonl                         # one record per question
    bench/data/manifest.json                              # summary of generated artifacts
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .generator import generate_one
from .ground_truth import generate_ground_truth, write_ground_truth
from .templates import TEMPLATES


def main(out_dir: str = "bench/data", sample_size: int = 3, seed: int = 42) -> None:
    base = Path(out_dir)
    files_dir = base / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    all_questions: list[dict] = []
    manifest: dict = {
        "templates": [],
        "n_files": 0,
        "n_questions_total": 0,
        "sample_size": sample_size,
        "seed": seed,
    }

    for spec in TEMPLATES:
        spec_summary = {
            "file_id": spec.file_id,
            "n_companies": spec.n_companies,
            "n_funds": spec.n_funds,
            "notes": spec.notes,
            "files": [],
        }
        for version in ("A", "B", "C"):
            xlsx_path = generate_one(spec, version, files_dir, seed=seed)
            df_path = files_dir / f"{spec.file_id}_{version}.canonical.parquet"
            canonical_df = pd.read_parquet(df_path)
            records = generate_ground_truth(
                canonical_df, spec.file_id, version, sample_size, seed
            )
            all_questions.extend(records)
            file_info = {
                "version": version,
                "xlsx": str(xlsx_path.relative_to(base)),
                "canonical": str(df_path.relative_to(base)),
                "n_rows": len(canonical_df),
                "n_questions": len(records),
            }
            spec_summary["files"].append(file_info)
            print(f"  ✓ {spec.file_id}_{version}: {len(canonical_df)} rows, "
                  f"{len(records)} questions")
        manifest["templates"].append(spec_summary)

    manifest["n_files"] = sum(len(t["files"]) for t in manifest["templates"])
    manifest["n_questions_total"] = len(all_questions)

    write_ground_truth(all_questions, base / "ground_truth.jsonl")
    (base / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    print()
    print(f"Generated {manifest['n_files']} xlsx files "
          f"across {len(TEMPLATES)} base templates × 3 versions.")
    print(f"Total questions: {manifest['n_questions_total']}")
    print(f"Ground truth: {base / 'ground_truth.jsonl'}")
    print(f"Manifest: {base / 'manifest.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="bench/data")
    parser.add_argument("--sample-size", type=int, default=3,
                        help="Number of entity samples for parameterized questions.")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    main(args.out_dir, args.sample_size, args.seed)
