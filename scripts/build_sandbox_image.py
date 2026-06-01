"""Build the FinSheet sandbox Docker image.

Usage:
    uv run python scripts/build_sandbox_image.py
    uv run python scripts/build_sandbox_image.py --tag finsheet-sandbox:dev

Equivalent to:
    docker build -t finsheet-sandbox:latest src/finsheet/mcp/runner_image

Run this once before starting the MCP server. The resulting image is
invoked once per execute_python tool call with strict security flags.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTEXT = ROOT / "src" / "finsheet" / "mcp" / "runner_image"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", default="finsheet-sandbox:latest")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()

    if shutil.which("docker") is None:
        print(
            "✗ docker CLI not found on PATH. Install Docker Desktop "
            "(Win/Mac) or Docker Engine (Linux) first.",
            file=sys.stderr,
        )
        return 1

    if not CONTEXT.exists():
        print(f"✗ Docker context not found at {CONTEXT}", file=sys.stderr)
        return 1

    cmd = ["docker", "build", "-t", args.tag]
    if args.no_cache:
        cmd.append("--no-cache")
    cmd.append(str(CONTEXT))

    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
