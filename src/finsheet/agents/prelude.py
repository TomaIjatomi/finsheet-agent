"""
Prelude builder for the Computation Agent.

The prelude is deterministic Python code that the orchestrator (not the LLM)
prepends to every sandbox call. It handles two universal concerns so the
LLM-generated code can stay focused on the question-specific logic:

  1. For row_separator layout: injects a `Fund` column into df using the
     SchemaCard's fund_boundaries. The LLM can then `df.groupby('Fund')`
     regardless of layout.
  2. Removes average/summary rows. These are NOT portfolio companies and
     11 of 16 question templates need them excluded.

After the prelude runs, `df` contains only portfolio companies and always
has a usable `Fund` column. The LLM-generated subgoal code can rely on
both invariants.

The prelude is built once per question (cached on the ComputationAgent) and
prepended verbatim to every subgoal's code before execution.
"""

from __future__ import annotations

from .types import SchemaCard


def build_prelude(schema_card: SchemaCard) -> str:
    """Generate the deterministic preamble for one workbook+sheet.

    Returns a Python source string. Embedding fund_boundaries inline (vs.
    passing as a named_range) keeps the sandbox tool-call interface simple:
    only `df` needs to be a named range.
    """
    parts: list[str] = []
    parts.append(
        "# ----- AUTO-GENERATED PRELUDE -----\n"
        "# Adapts df to the workbook structure so subgoal code can rely on:\n"
        "#   - df['Fund'] is always populated\n"
        "#   - df contains only portfolio companies (no average/summary rows)\n"
    )

    if schema_card.fund_layout == "row_separator":
        # Build a Python literal for fund_boundaries that's safe to inline.
        boundaries_repr = "[\n"
        for fb in schema_card.funds:
            boundaries_repr += (
                f"    {{'fund': {fb.fund!r}, "
                f"'start_row': {fb.start_row}, 'end_row': {fb.end_row}}},\n"
            )
        boundaries_repr += "]"

        parts.append(
            f"# Layout is 'row_separator' — inject Fund column from row spans.\n"
            f"FUND_BOUNDARIES = {boundaries_repr}\n"
            f"DATA_START_ROW = {schema_card.data_start_row}\n"
            "\n"
            "def _fund_for_pandas_idx(idx):\n"
            "    sheet_row = idx + DATA_START_ROW\n"
            "    for fb in FUND_BOUNDARIES:\n"
            "        if fb['start_row'] <= sheet_row <= fb['end_row']:\n"
            "            return fb['fund']\n"
            "    return None\n"
            "\n"
            "df['Fund'] = [_fund_for_pandas_idx(i) for i in range(len(df))]\n"
        )
    elif schema_card.fund_layout == "column" and schema_card.fund_column:
        parts.append(
            f"# Layout is 'column' — Fund column already exists at "
            f"{schema_card.fund_column!r}. No injection needed.\n"
        )
    else:
        parts.append("# Layout is 'unknown' — agent code must handle fund logic explicitly.\n")

    # Average-row removal. We do this AFTER fund labeling so row indices line up.
    parts.append(
        "# Remove non-company rows. Real portfolio companies have many populated\n"
        "# columns; fund-divider rows (row_separator layout only) have ONLY a\n"
        "# fund name in the Company column; average rows end with 'Average'.\n"
        "_min_populated = 5\n"
        "_mask = df.notna().sum(axis=1) >= _min_populated\n"
        "_mask &= ~df['Company'].astype(str).str.strip().str.endswith('Average', na=False)\n"
        "df = df[_mask].reset_index(drop=True)\n"
        "# ----- END PRELUDE -----\n"
    )

    return "\n".join(parts)
