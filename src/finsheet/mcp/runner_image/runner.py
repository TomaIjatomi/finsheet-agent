"""In-container runner script for the Computation Agent's sandbox.

Reads /data/inputs.json to get the user code + names of DataFrames to load,
loads each as parquet from /data/<name>.parquet, exec's the code in a
namespace that includes pandas + numpy + the named DataFrames, and prints
a single JSON line on stdout with {result, stdout, stderr, error}.

The wrapping pattern: the user's last expression should land in __result__.
Convention: code may set __result__ explicitly. If not set, the runner
attempts to evaluate the last non-empty line of the code as an expression.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path("/data")
INPUTS_FILE = DATA_DIR / "inputs.json"


def _serialize(value):
    """Mirror of sandbox._serialize_result, kept in sync deliberately."""
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
        records = value.to_dict(orient="records")
        return [{k: _serialize(v) for k, v in r.items()} for r in records]
    if isinstance(value, pd.Series):
        return {str(k): _serialize(v) for k, v in value.to_dict().items()}
    if isinstance(value, np.ndarray):
        return [_serialize(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(v) for v in value]
    return str(value)


def main() -> int:
    try:
        inputs = json.loads(INPUTS_FILE.read_text())
    except Exception as e:
        print(
            json.dumps(
                {
                    "result": None,
                    "stdout": "",
                    "stderr": "",
                    "error": f"failed to read inputs: {type(e).__name__}: {e}",
                }
            )
        )
        return 2

    code: str = inputs.get("code", "")
    df_names: list[str] = inputs.get("dataframes", [])

    namespace = {"pd": pd, "np": np, "__result__": None}
    try:
        for name in df_names:
            path = DATA_DIR / f"{name}.parquet"
            namespace[name] = pd.read_parquet(path)
    except Exception as e:
        print(
            json.dumps(
                {
                    "result": None,
                    "stdout": "",
                    "stderr": "",
                    "error": f"failed to load parquet: {type(e).__name__}: {e}",
                }
            )
        )
        return 2

    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    error: str | None = None
    try:
        wrapped = textwrap.dedent("__result__ = None\n") + code
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            exec(compile(wrapped, "<user_code>", "exec"), namespace)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    payload = {
        "result": _serialize(namespace.get("__result__")),
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "error": error,
    }
    print(json.dumps(payload))
    return 0 if error is None else 1


if __name__ == "__main__":
    sys.exit(main())
