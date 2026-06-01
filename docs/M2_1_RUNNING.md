# M2.1 — Spreadsheet MCP Server

The foundational architectural piece for Week 2. Exposes five tools over MCP that the agent stack (M2.2 onwards) will use to introspect workbooks, read cells, execute pandas code in a sandboxed container, and format citations.

## What this is

```
src/finsheet/mcp/
├── workbook.py        # schema inference, range loading (pure Python)
├── sandbox.py         # DockerSandbox + LocalSandbox (test-only)
├── server.py          # FastMCP server + 5 tool functions
└── runner_image/      # the Docker image context
    ├── Dockerfile     # python:3.12-slim + pandas/numpy/pyarrow
    └── runner.py      # in-container script that exec's user code

scripts/
├── start_mcp_server.py        # boots the server over stdio
└── build_sandbox_image.py     # builds the Docker image
```

The five tools:

| Tool | Purpose | Who uses it (M2) |
|---|---|---|
| `list_sheets(file_path)` | enumerate sheets + dimensions | Orchestrator boot |
| `get_sheet_schema(file_path, sheet)` | columns, dtypes, fund layout, fund boundaries, sample rows | Schema Agent |
| `get_range(file_path, sheet, range)` | cell values for one range | Verification Agent (spot-check) |
| `execute_python(file_path, code, named_ranges)` | sandboxed pandas/numpy execution | Computation Agent |
| `cite_cells(claim, sheet, cells)` | format `[Sheet!A1,A2,A3]` citations | Synthesizer, Verification |

## Prerequisites

- M1.1 done (uv-managed env, `.env` configured)
- Docker installed and running (Docker Desktop on Win/Mac, Docker Engine on Linux)
- Agent deps installed: `uv sync --extra dev --extra agents`

Verify Docker is alive:

```powershell
docker run --rm hello-world
```

## 1. Build the sandbox image (one-time, ~60s)

```powershell
uv run python scripts/build_sandbox_image.py
```

This builds `finsheet-sandbox:latest`. Output ends with:

```
=> => writing image sha256:...
=> => naming to docker.io/library/finsheet-sandbox:latest
```

Verify:

```powershell
docker images finsheet-sandbox
```

## 2. Start the MCP server

```powershell
uv run python scripts/start_mcp_server.py
```

The server runs on stdio (the transport MCP clients connect to via subprocess) and waits for client connections. It's stateless — every tool call passes the xlsx path, so the same server can serve any workbook.

You'll see: `FinSheet MCP server ready (sandbox: docker)` on stderr, then the process waits.

The server doesn't print to stdout — that's the MCP protocol channel. Ctrl-C to stop.

For a quick sanity test without an MCP client, use the LocalSandbox in-process (the next section).

## 3. Sanity-check the tool surface in-process (no MCP client needed)

```powershell
uv run python -c @"
from finsheet.mcp.sandbox import make_sandbox
from finsheet.mcp.server import tool_list_sheets, tool_get_sheet_schema, tool_execute_python

file = 'bench/data/files/synthetic4_A.xlsx'

print('--- list_sheets ---')
print(tool_list_sheets(file))

print('--- schema (first fund boundary) ---')
schema = tool_get_sheet_schema(file)
print('fund_layout:', schema['fund_layout'])
print('first fund:', schema['fund_boundaries'][0])

print('--- execute_python (deterministic compute) ---')
sb = make_sandbox(prefer='local_unsafe', allow_unsafe=True)
result = tool_execute_python(
    file_path=file,
    code='__result__ = len(df[df[\"Status\"] == \"Unrealized\"])',
    named_ranges={'df': {'sheet': 'Portfolio', 'range': 'A4:N200'}},
    sandbox=sb,
)
print('unrealized count:', result['result'])
"@
```

Expected output:

```
--- list_sheets ---
{'sheets': {'Portfolio': {'rows': 179, 'cols': 13}}}
--- schema (first fund boundary) ---
fund_layout: row_separator
first fund: {'fund': 'Fund I', 'start_row': 6, 'end_row': 22, 'n_companies': 15}
--- execute_python (deterministic compute) ---
unrealized count: 37
```

## 4. Verify the Docker sandbox actually works (heavier test)

```powershell
uv run python -c @"
import pandas as pd
from finsheet.mcp.sandbox import make_sandbox

sb = make_sandbox(prefer='docker')
df = pd.DataFrame({'Fund': ['I', 'I', 'II'], 'EV': [100, 200, 300]})
result = sb.execute(
    code='__result__ = df.groupby(\"Fund\")[\"EV\"].sum().to_dict()',
    dataframes={'df': df},
)
print('result:', result.result)
print('error:', result.error)
print('exit:', result.exit_code)
"@
```

Expected: `result: {'I': 300, 'II': 300}` with no error, exit_code 0. Cold start ~500-800ms; warm start ~200ms.

If you get `Docker not found`: Docker Desktop isn't running. Start it from the system tray and retry.

If the call times out: the image may not have been built. Run step 1 again.

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: Docker not found on PATH` | Docker not installed or not running | Install Docker Desktop; verify `docker run hello-world` works |
| `KeyError: 'Entry EV'` in execute_python | Used non-canonical column name | Read column names from `get_sheet_schema` first; multi-line headers collapse to `"Entry Enterprise Value"` |
| `Invalid range: 'foo'` | Range not in `A1:B10` format | Range syntax is strict — uppercase letters, colon-separated |
| Sandbox returns `None` result | Code didn't set `__result__` | Code must explicitly assign to `__result__`; the runner returns whatever's in that variable |
| Docker image pull is slow | First-time pull of python:3.12-slim | One-time, ~200MB; subsequent builds are cached |

## What's next (M2.2-2.5)

The MCP server is now a stable foundation. M2.2-2.5 build the agent stack on top:

- **M2.2 — Schema Agent + Query Decomposition**: Gemini Flash agent that calls `get_sheet_schema` and produces a structured representation; Gemini 2.5 Pro agent that takes question + schema and produces an ordered list of computational subgoals.
- **M2.3 — Computation Agent + Fact Sheet**: Gemini 2.5 Pro generates pandas code per subgoal, calls `execute_python` via MCP, captures results into a key-value Fact Sheet. **Never emits numbers that didn't come from a tool call.**
- **M2.4 — Verification Agent**: independently reads source cells via `get_range`, sanity-checks Computation outputs (arithmetic drift, missed cells, scale mismatches), triggers revision if needed.
- **M2.5 — Tracing + cost capture**: OpenTelemetry across all agents + the MCP server. Langfuse for replay.

After M2.5, you'll be ready for M3 — full eval against the same 528-question bench, dashboard, and demo.
