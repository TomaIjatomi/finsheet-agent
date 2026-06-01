"""FinSheet Spreadsheet MCP server.

The deterministic tool surface for the agent stack. Exposes five tools
over MCP that handle workbook introspection, range reading, sandboxed
code execution, and citation formatting.

Modules:
  workbook  — pure-Python xlsx loading + schema inference (no Docker)
  sandbox   — code-execution sandboxes (DockerSandbox + LocalSandbox)
  server    — FastMCP server wiring + the five tool functions
"""

from .sandbox import DockerSandbox, LocalSandbox, Sandbox, SandboxResult, make_sandbox
from .server import (
    build_server,
    tool_cite_cells,
    tool_execute_python,
    tool_get_range,
    tool_get_sheet_schema,
    tool_list_sheets,
)
from .workbook import (
    ColumnInfo,
    FundBoundary,
    SheetSchema,
    format_citation,
    get_range_as_dict,
    get_sheet_schema,
    list_sheets,
    load_range_as_df,
)

__all__ = [
    # workbook
    "ColumnInfo",
    "FundBoundary",
    "SheetSchema",
    "list_sheets",
    "get_sheet_schema",
    "get_range_as_dict",
    "load_range_as_df",
    "format_citation",
    # sandbox
    "Sandbox",
    "SandboxResult",
    "DockerSandbox",
    "LocalSandbox",
    "make_sandbox",
    # server
    "build_server",
    "tool_list_sheets",
    "tool_get_sheet_schema",
    "tool_get_range",
    "tool_execute_python",
    "tool_cite_cells",
]
