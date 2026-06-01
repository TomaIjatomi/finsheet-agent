"""Start the FinSheet Spreadsheet MCP server.

Usage:
    uv run python scripts/start_mcp_server.py

By default, runs on stdio (the transport MCP clients connect to via
subprocess). The server is stateless — each tool call passes the
workbook path, so a single server instance serves any xlsx.

Requires Docker if the Computation Agent will be invoked. Build the
sandbox image first:
    uv run python scripts/build_sandbox_image.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from finsheet.mcp.sandbox import make_sandbox  # noqa: E402
from finsheet.mcp.server import build_server  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--sandbox",
        choices=["docker", "local_unsafe"],
        default="docker",
        help="Code-execution sandbox. 'docker' is the production choice; "
        "'local_unsafe' runs code in the server process and is only "
        "for development without Docker — never use with untrusted code.",
    )
    p.add_argument(
        "--image",
        default="finsheet-sandbox:latest",
        help="Docker image to use (only relevant with --sandbox=docker).",
    )
    args = p.parse_args()

    if args.sandbox == "docker":
        sandbox = make_sandbox(prefer="docker", image=args.image)
    else:
        print(
            "⚠ Starting MCP server with LocalSandbox. "
            "This is UNSAFE for any LLM-generated code. Dev / debug only.",
            file=sys.stderr,
        )
        sandbox = make_sandbox(prefer="local_unsafe", allow_unsafe=True)

    print(f"FinSheet MCP server ready (sandbox: {sandbox.name})", file=sys.stderr)
    mcp = build_server(sandbox=sandbox)
    mcp.run()  # stdio transport by default
    return 0


if __name__ == "__main__":
    sys.exit(main())
