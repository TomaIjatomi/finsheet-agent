"""
Python code execution sandbox for the Computation Agent.

Two implementations behind a single Protocol:

  DockerSandbox  — production. Each call spins a short-lived container
                   with read-only FS, no network, capped memory/CPU,
                   non-root user. The agent never executes code in the
                   server process.

  LocalSandbox   — testing only. Runs code in the current process via
                   exec(). UNSAFE — only use with code you control
                   (i.e. tests). Selected explicitly; the server will
                   refuse to use it unless allow_unsafe=True.

The Sandbox Protocol takes named DataFrames + Python code and returns
{stdout, stderr, error, result}. The Computation Agent uses the result
field to capture computed values into the Fact Sheet.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd


@dataclass
class SandboxResult:
    """Output from one execute() call."""

    result: object | None
    stdout: str
    stderr: str
    error: str | None
    exit_code: int

    def to_dict(self) -> dict:
        return {
            "result": self.result,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "exit_code": self.exit_code,
        }


class Sandbox(Protocol):
    """Execution surface for LLM-generated pandas/numpy code."""

    name: str

    def execute(
        self, code: str, dataframes: dict[str, pd.DataFrame], timeout_s: int = 30
    ) -> SandboxResult: ...


# ---- Result serialization (shared) --------------------------------------


def _serialize_result(value) -> object:
    """Convert Python/pandas values to JSON-friendly types.

    DataFrames → list-of-records. Series → dict. ndarrays → list.
    NaN/NaT/Inf → None. Tuples and sets → lists. Everything else stringified.
    """
    if value is None:
        return None
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if np.isnan(value) or np.isinf(value):
            return None
        return value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        if np.isnan(v) or np.isinf(v):
            return None
        return v
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.DataFrame):
        # to_dict can produce NaN; clean it
        records = value.to_dict(orient="records")
        return [{k: _serialize_result(v) for k, v in r.items()} for r in records]
    if isinstance(value, pd.Series):
        return {str(k): _serialize_result(v) for k, v in value.to_dict().items()}
    if isinstance(value, np.ndarray):
        return [_serialize_result(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {str(k): _serialize_result(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_result(v) for v in value]
    return str(value)


# ---- LocalSandbox (testing only) ----------------------------------------


class LocalSandbox:
    """Runs code in the current Python process via exec(). UNSAFE.

    Only acceptable for unit tests where the test author controls the code.
    Never connect this to LLM-generated code in production. The server
    refuses to instantiate it unless allow_unsafe=True is passed explicitly.
    """

    name = "local_unsafe"

    def __init__(self, allow_unsafe: bool = False):
        if not allow_unsafe:
            raise RuntimeError(
                "LocalSandbox is unsafe for LLM-generated code. "
                "Pass allow_unsafe=True if this is a test."
            )

    def execute(
        self, code: str, dataframes: dict[str, pd.DataFrame], timeout_s: int = 30
    ) -> SandboxResult:  # noqa: ARG002
        namespace = {
            "pd": pd,
            "np": np,
            **{name: df.copy() for name, df in dataframes.items()},
        }
        stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
        error: str | None = None
        result_val: object | None = None
        try:
            # Wrap the code so the last expression's value lands in __result__
            # If the user has set __result__ explicitly, we use that.
            wrapped = (
                textwrap.dedent("""
            __result__ = None
            """)
                + code
            )
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(compile(wrapped, "<sandbox>", "exec"), namespace)
            result_val = namespace.get("__result__")
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        return SandboxResult(
            result=_serialize_result(result_val),
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            error=error,
            exit_code=1 if error else 0,
        )


# ---- DockerSandbox (production) -----------------------------------------

DOCKER_IMAGE_DEFAULT = "finsheet-sandbox:latest"


class DockerSandbox:
    """Runs each call in a short-lived Docker container.

    Security flags per call:
      --rm, --read-only, --network=none, --user=sandbox,
      --memory=512m, --cpus=1, --tmpfs /tmp:size=64m
    Data dir is mounted read-only at /data.
    """

    name = "docker"

    def __init__(self, image: str = DOCKER_IMAGE_DEFAULT, memory: str = "512m", cpus: str = "1"):
        if shutil.which("docker") is None:
            raise RuntimeError(
                "Docker not found on PATH. Install Docker Desktop (Win/Mac) "
                "or Docker Engine (Linux), then `docker pull` or build the "
                "sandbox image."
            )
        self._image = image
        self._memory = memory
        self._cpus = cpus

    def _docker_run_args(self, data_mount: Path) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--network=none",
            "--user=sandbox",
            f"--memory={self._memory}",
            f"--cpus={self._cpus}",
            "--tmpfs",
            "/tmp:size=64m,exec",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{data_mount}:/data:ro",
            self._image,
        ]

    def execute(
        self, code: str, dataframes: dict[str, pd.DataFrame], timeout_s: int = 30
    ) -> SandboxResult:
        with tempfile.TemporaryDirectory(prefix="finsheet-sandbox-") as tmp:
            data_dir = Path(tmp)
            # Serialize each DataFrame to parquet
            for name, df in dataframes.items():
                if not name.isidentifier():
                    return SandboxResult(
                        result=None,
                        stdout="",
                        stderr="",
                        error=f"Invalid DataFrame name: {name!r}; must be a valid identifier",
                        exit_code=2,
                    )
                df.to_parquet(data_dir / f"{name}.parquet", index=False)
            # Inputs file the in-container runner reads
            inputs = {
                "code": code,
                "dataframes": list(dataframes.keys()),
            }
            (data_dir / "inputs.json").write_text(json.dumps(inputs))

            cmd = self._docker_run_args(data_dir)
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(
                    result=None,
                    stdout="",
                    stderr="",
                    error=f"timeout after {timeout_s}s",
                    exit_code=124,
                )

            stdout = proc.stdout
            stderr = proc.stderr
            # Last line of stdout is the JSON output from the runner
            payload_line = stdout.strip().split("\n")[-1] if stdout.strip() else ""
            payload: dict = {}
            parse_err: str | None = None
            try:
                payload = json.loads(payload_line) if payload_line else {}
            except json.JSONDecodeError as e:
                parse_err = (
                    f"runner did not return JSON on last stdout line: {e}. "
                    f"raw stdout: {stdout[:500]!r}"
                )

            if parse_err:
                return SandboxResult(
                    result=None,
                    stdout=stdout,
                    stderr=stderr,
                    error=parse_err,
                    exit_code=proc.returncode,
                )

            return SandboxResult(
                result=payload.get("result"),
                stdout=payload.get("stdout", ""),
                stderr=payload.get("stderr", "") or stderr,
                error=payload.get("error"),
                exit_code=proc.returncode,
            )


# ---- Factory ------------------------------------------------------------


def make_sandbox(prefer: str = "auto", **kwargs) -> Sandbox:
    """Construct a sandbox.

    prefer:
      'docker'        — only Docker; raise if unavailable
      'local_unsafe'  — only LocalSandbox; requires allow_unsafe=True kwarg
      'auto'          — use Docker if available, else raise (safer default)
    """
    if prefer == "local_unsafe":
        return LocalSandbox(**kwargs)
    if prefer in ("docker", "auto"):
        try:
            return DockerSandbox(**{k: v for k, v in kwargs.items() if k != "allow_unsafe"})
        except RuntimeError as e:
            if prefer == "docker":
                raise
            print(f"Sandbox: {e}", file=sys.stderr)
            raise
    raise ValueError(f"Unknown sandbox preference: {prefer}")
