"""
FinSheet Spreadsheet MCP Server.

Exposes five tools to LLM agents over MCP (Model Context Protocol):

  list_sheets(file_path)                       → workbook listing
  get_sheet_schema(file_path, sheet)            → schema + fund layout + samples
  get_range(file_path, sheet, range)            → cell values for a range
  execute_python(file_path, code, named_ranges) → sandboxed pandas/numpy execution
  cite_cells(claim, sheet, cells)               → format a structured citation

Stateless: every tool takes file_path as an argument, so a single server
instance can serve any xlsx. This is the architectural property that makes
the surface portable beyond this project (D12, D14).

Production sandbox: Docker per execution. Tests use LocalSandbox via the
make_sandbox factory.

Usage:
  uv run python scripts/start_mcp_server.py
or via stdio attached to an MCP-aware client.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .sandbox import Sandbox, SandboxResult, make_sandbox
from .workbook import (
    format_citation,
    get_range_as_dict,
    get_sheet_schema,
    list_sheets,
    load_range_as_df,
)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - install via `uv sync --extra agents`
    FastMCP = None  # type: ignore[assignment]


# ---- Pure-function tool implementations (importable, testable) ----------


def tool_list_sheets(file_path: str) -> dict:
    """List sheets in an xlsx workbook with row/column dimensions."""
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    return {"sheets": list_sheets(path)}


def tool_get_sheet_schema(file_path: str, sheet: str | None = None) -> dict:
    """Infer schema for a sheet: headers, columns + dtypes, fund layout,
    fund boundaries, average-row indices, and sample rows.

    The agent should call this once per (file, sheet) and cache the result
    for the duration of the query — the structure doesn't change.
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    try:
        schema = get_sheet_schema(path, sheet=sheet)
        return schema.to_dict()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_get_range(file_path: str, sheet: str, range: str) -> dict:  # noqa: A002
    """Read a cell range and return its values keyed by cell coordinate.

    Use this to spot-check specific cells or verify computations. For
    bulk computation, use execute_python with named ranges instead.
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    try:
        return get_range_as_dict(path, sheet, range)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_execute_python(
    file_path: str,
    code: str,
    named_ranges: dict[str, dict],
    sandbox: Sandbox | None = None,
    timeout_s: int = 30,
) -> dict:
    """Run pandas/numpy code over named ranges in a sandbox.

    Args:
      file_path: workbook path.
      code: Python source. Set the final answer into `__result__` to return it.
      named_ranges: e.g. {"df": {"sheet": "Portfolio", "range": "A4:N156"}}.
        First row of each range is treated as headers; multi-line headers
        are collapsed to single line for column names.
      sandbox: injected for testing. In production, the server creates
        a DockerSandbox at startup.
      timeout_s: per-call wall-clock cap.

    Returns:
      {result, stdout, stderr, error, exit_code}. Errors that prevented
      execution (e.g. invalid range, missing file) come back in `error`
      with no result. Errors raised inside the code come back with stderr.
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if sandbox is None:
        return {"error": "no sandbox configured; this should never happen in production"}

    # Resolve named ranges into DataFrames in the server process
    dataframes: dict[str, pd.DataFrame] = {}
    try:
        for name, spec in named_ranges.items():
            if not name.isidentifier():
                return {"error": f"Invalid name {name!r}: must be a Python identifier"}
            sheet = spec["sheet"]
            range_str = spec["range"]
            dataframes[name] = load_range_as_df(path, sheet, range_str)
    except (KeyError, ValueError) as e:
        return {"error": f"failed to resolve named ranges: {e}"}

    result: SandboxResult = sandbox.execute(code, dataframes, timeout_s=timeout_s)
    return result.to_dict()


def tool_cite_cells(claim: str, sheet: str, cells: list[str]) -> str:
    """Format a claim with a structured cell-range citation.

    Used by the Verification Agent and Synthesizer so every numerical
    claim in the final answer is traceable back to specific source cells.
    """
    return format_citation(claim, sheet, cells)


# ---- MCP server wiring --------------------------------------------------


def build_server(sandbox: Sandbox | None = None) -> FastMCP:
    """Construct the MCP server with the five tools bound to a sandbox."""
    if FastMCP is None:
        raise RuntimeError("mcp package not installed. Run: uv sync --extra agents")
    sb = sandbox or make_sandbox(prefer="docker")
    mcp = FastMCP("finsheet-spreadsheet")

    @mcp.tool()
    def list_sheets_tool(file_path: str) -> str:
        """List the sheets in an xlsx file with their row/column dimensions."""
        return json.dumps(tool_list_sheets(file_path))

    @mcp.tool()
    def get_sheet_schema_tool(file_path: str, sheet: str | None = None) -> str:
        """Get the schema for one sheet of an xlsx file. Includes columns,
        dtypes, fund layout, fund boundaries (which rows belong to which fund),
        average/summary row indices, and a sample of the first data rows.

        Call this once per (file, sheet) and reuse the result — the schema
        doesn't change within a single query.
        """
        return json.dumps(tool_get_sheet_schema(file_path, sheet))

    @mcp.tool()
    def get_range_tool(file_path: str, sheet: str, range: str) -> str:  # noqa: A002
        """Read a cell range like 'A4:N20' from a sheet and return the cell
        values keyed by cell coordinate (e.g., 'G6': 478.3). Use for
        spot-checking specific cells; for bulk computation use execute_python.
        """
        return json.dumps(tool_get_range(file_path, sheet, range))

    @mcp.tool()
    def execute_python_tool(
        file_path: str,
        code: str,
        named_ranges: dict[str, dict],
    ) -> str:
        """Execute pandas/numpy code over named cell ranges in a sandboxed
        container.

        named_ranges maps DataFrame variable names to range specs:
          {"df": {"sheet": "Portfolio", "range": "A4:N156"}}

        The first row of each range becomes the column headers; multi-line
        headers are collapsed to single line.

        Set the final answer into a variable named __result__ to return it.
        Example code:

          __result__ = df.groupby("Fund")["Entry EV"].sum().to_dict()

        Returns {result, stdout, stderr, error, exit_code}.
        """
        return json.dumps(
            tool_execute_python(
                file_path,
                code,
                named_ranges,
                sandbox=sb,
            )
        )

    @mcp.tool()
    def cite_cells_tool(claim: str, sheet: str, cells: list[str]) -> str:
        """Format a claim string with a structured cell-range citation.
        Every numerical answer in the final response should include a
        citation produced by this tool.

        Example:
          cite_cells_tool('Apex has Entry EV $478M', 'Portfolio', ['G6'])
          → 'Apex has Entry EV $478M [Portfolio!G6]'
        """
        return tool_cite_cells(claim, sheet, cells)

    return mcp
