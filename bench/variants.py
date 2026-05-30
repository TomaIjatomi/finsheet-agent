"""
B and C structural variants for each base template.

Following FinSheet-Bench (Ravnik et al. 2026) Section 3.1.3:
- Roughly one-third of rows are removed at random
- Structural modifications are applied (column splits, separator changes, etc.)

For ground-truth purposes, variants operate on the canonical DataFrame
BEFORE the xlsx is rendered, so the question answers match what the
LLM sees in the file.
"""
from __future__ import annotations

import random
from dataclasses import replace

import pandas as pd

from .templates import TemplateSpec


def apply_variant(df: pd.DataFrame, version: str) -> pd.DataFrame:
    """Apply variant-specific row removal to the canonical DataFrame."""
    rng = random.Random(hash(version + "rows") % 2**31)
    if version == "B":
        # Remove ~1/3 of rows at random, but keep at least 3 per fund
        keep_idx = []
        for _fund, group in df.groupby("Fund", sort=False):
            n = len(group)
            keep_n = max(3, int(n * 2 / 3))
            idxs = list(group.index)
            rng.shuffle(idxs)
            keep_idx.extend(idxs[:keep_n])
        df = df.loc[sorted(keep_idx)].reset_index(drop=True)
    elif version == "C":
        # Remove ~1/4 of rows; different from B to ensure C != B
        keep_idx = []
        for _fund, group in df.groupby("Fund", sort=False):
            n = len(group)
            keep_n = max(3, int(n * 3 / 4))
            idxs = list(group.index)
            rng.shuffle(idxs)
            keep_idx.extend(idxs[:keep_n])
        df = df.loc[sorted(keep_idx)].reset_index(drop=True)
    return df


def variant_overrides(spec: TemplateSpec, version: str) -> TemplateSpec:
    """Return a variant of the spec with structural modifications applied.

    B variants change list separators and toggle some layout flags.
    C variants add average rows where they weren't, and may swap fund as column/row.
    """
    if version == "B":
        return replace(
            spec,
            list_separator=", " if spec.list_separator == "; " else "; ",
            has_average_rows=False,  # B drops average rows if present
            blank_rows_between_funds=not spec.blank_rows_between_funds,
        )
    if version == "C":
        return replace(
            spec,
            has_average_rows=True,   # C always has average rows
            fund_as_column=not spec.fund_as_column,  # invert fund placement
            list_separator=", " if spec.list_separator == "; " else "; ",
        )
    return spec
