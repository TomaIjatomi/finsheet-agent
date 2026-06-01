"""
Schema Agent — produces the SchemaCard.

Deliberately deterministic, no LLM call. The MCP `get_sheet_schema` tool
already produces structurally rich output; wrapping it in an LLM call to
"summarize" would just add a hallucination surface and a token bill.

The Schema Agent's job is to:
  1. Call `tool_get_sheet_schema` for the (file, sheet).
  2. Convert the raw dict into a typed SchemaCard (Pydantic).
  3. Derive convenience fields the downstream agents will need:
       - data_range as an Excel range string
       - structural notes that flag quirks (multi-line headers, mixed
         dtypes, fund-as-row-separator layout) in plain English

Tests can run without Vertex AI, GCP, or any network — the schema work
is all openpyxl + pandas.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl.utils import get_column_letter

from ..mcp.server import tool_get_sheet_schema
from .types import ColumnSummary, FundSummary, SchemaCard


class SchemaAgent:
    """Deterministic. Builds a SchemaCard for one (file, sheet)."""

    name = "schema_agent"

    def profile(self, file_path: str | Path, sheet: str | None = None) -> SchemaCard:
        """Build the SchemaCard. Raises ValueError on file_not_found or
        schema-inference failure; the caller decides how to surface that."""
        raw = tool_get_sheet_schema(str(file_path), sheet)
        if "error" in raw:
            raise ValueError(f"schema introspection failed: {raw['error']}")

        n_cols = raw["dimensions"]["cols"]
        last_col_letter = get_column_letter(n_cols)
        data_range = f"A{raw['header_row']}:{last_col_letter}{raw['data_end_row']}"

        columns = [
            ColumnSummary(name=c["name"], col_letter=c["col"], dtype=c["dtype"])
            for c in raw["columns"]
        ]
        funds = [
            FundSummary(
                fund=fb["fund"],
                start_row=fb["start_row"],
                end_row=fb["end_row"],
                n_companies=fb["n_companies"],
            )
            for fb in raw["fund_boundaries"]
        ]

        notes = self._build_structural_notes(raw, columns)

        return SchemaCard(
            file_path=str(file_path),
            sheet_name=raw["name"],
            n_rows=raw["dimensions"]["rows"],
            n_cols=n_cols,
            header_row=raw["header_row"],
            data_start_row=raw["data_start_row"],
            data_end_row=raw["data_end_row"],
            data_range=data_range,
            columns=columns,
            fund_layout=raw["fund_layout"],
            fund_column=raw.get("fund_column"),
            funds=funds,
            average_rows=raw.get("average_rows", []),
            structural_notes=notes,
        )

    # --- helpers ---

    @staticmethod
    def _build_structural_notes(raw: dict, columns: list[ColumnSummary]) -> list[str]:
        """Produce plain-English notes that the Decomposition Agent will
        include in its prompt so the LLM is aware of structural quirks
        before planning."""
        notes: list[str] = []

        # Multi-line headers got collapsed: any column name containing a space
        # could originally have been multi-line. Flag the most likely candidates
        # so the planner knows column names may look unfamiliar.
        wordy = [c.name for c in columns if " " in c.name and len(c.name.split()) >= 2]
        if wordy:
            sample = ", ".join(f"'{w}'" for w in wordy[:3])
            notes.append(
                f"Column names may be collapsed multi-line headers "
                f"(e.g. {sample}). Use them EXACTLY as listed; do not "
                f"abbreviate (e.g. write 'Entry Enterprise Value', not 'Entry EV')."
            )

        if raw["fund_layout"] == "row_separator":
            notes.append(
                "Fund layout is 'row_separator': each fund's companies are "
                "preceded by a divider row where only column A contains the "
                "fund name. There is NO 'Fund' column — to group by fund in "
                "pandas, use the fund_boundaries to slice the DataFrame."
            )
        elif raw["fund_layout"] == "column":
            notes.append(
                f"Fund layout is 'column': the Fund column is at "
                f"{raw.get('fund_column')!r}. Group by it directly with pandas."
            )

        if raw.get("average_rows"):
            notes.append(
                f"There are {len(raw['average_rows'])} average/summary rows "
                f"interleaved with company data (rows: "
                f"{raw['average_rows'][:5]}{'...' if len(raw['average_rows']) > 5 else ''}). "
                f"These are NOT portfolio companies. Exclude them by filtering "
                f"on the Company column being non-null AND not ending in 'Average'."
            )

        return notes
