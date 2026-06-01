"""Tests for the Spreadsheet MCP server.

Covers:
  - Workbook introspection (list_sheets, get_sheet_schema, get_range)
    against the synthetic bench files
  - LocalSandbox code execution (Docker is mocked separately)
  - Tool wrapper functions (file_not_found handling, named-range resolution)
  - DockerSandbox argument construction (no actual Docker call needed)

Docker integration is verified manually via docs/M2_1_RUNNING.md.
Marking heavy integration tests with @pytest.mark.docker so they can
be skipped in CI environments without Docker.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from finsheet.mcp.sandbox import (
    DockerSandbox,
    LocalSandbox,
    SandboxResult,
    make_sandbox,
)
from finsheet.mcp.server import (
    tool_cite_cells,
    tool_execute_python,
    tool_get_range,
    tool_get_sheet_schema,
    tool_list_sheets,
)
from finsheet.mcp.workbook import (
    format_citation,
    get_range_as_dict,
    get_sheet_schema,
    list_sheets,
    load_range_as_df,
)

BENCH_FILES = Path("bench/data/files")


def _need_bench():
    if not BENCH_FILES.exists():
        pytest.skip("Bench not built; run `uv run python -m bench.build`")


# ---- Workbook: list_sheets ----------------------------------------------


def test_list_sheets_returns_portfolio():
    _need_bench()
    sheets = list_sheets(BENCH_FILES / "synthetic1_A.xlsx")
    assert "Portfolio" in sheets
    assert sheets["Portfolio"]["rows"] > 40
    assert sheets["Portfolio"]["cols"] >= 12


# ---- Workbook: get_sheet_schema -----------------------------------------


def test_schema_detects_fund_column_layout():
    """synthetic1 uses Fund as a column."""
    _need_bench()
    schema = get_sheet_schema(BENCH_FILES / "synthetic1_A.xlsx")
    assert schema.fund_layout == "column"
    assert schema.fund_column is not None
    assert len(schema.fund_boundaries) == 4
    total = sum(fb.n_companies for fb in schema.fund_boundaries)
    assert total == 45  # synthetic1 has 45 companies


def test_schema_detects_row_separator_layout():
    """synthetic3 / synthetic4 use Fund as a row divider."""
    _need_bench()
    schema = get_sheet_schema(BENCH_FILES / "synthetic4_A.xlsx")
    assert schema.fund_layout == "row_separator"
    assert schema.fund_column is None
    assert len(schema.fund_boundaries) == 8
    total = sum(fb.n_companies for fb in schema.fund_boundaries)
    assert total == 152


def test_schema_finds_header_and_columns():
    _need_bench()
    schema = get_sheet_schema(BENCH_FILES / "synthetic1_A.xlsx")
    col_names = [c.name for c in schema.columns]
    assert "Company" in col_names
    assert any("Entry EV" in n for n in col_names)
    assert any("EBITDA" in n for n in col_names)
    # Sample rows present
    assert len(schema.sample_rows) >= 1
    assert "Company" in schema.sample_rows[0]


def test_schema_detects_average_rows():
    """synthetic2 has 'Fund X Average' rows after each fund block."""
    _need_bench()
    schema = get_sheet_schema(BENCH_FILES / "synthetic2_A.xlsx")
    assert len(schema.average_rows) >= 3  # at least one average row per fund


def test_schema_dict_serializable():
    _need_bench()
    schema = get_sheet_schema(BENCH_FILES / "synthetic1_A.xlsx")
    d = schema.to_dict()
    # Round-trip through JSON works (must be fully JSON-friendly)
    s = json.dumps(d, default=str)
    parsed = json.loads(s)
    assert parsed["name"] == "Portfolio"
    assert parsed["fund_layout"] == "column"


# ---- Workbook: range loading --------------------------------------------


def test_get_range_returns_cell_dict():
    _need_bench()
    out = get_range_as_dict(BENCH_FILES / "synthetic1_A.xlsx", "Portfolio", "A4:D6")
    assert out["range"] == "A4:D6"
    assert len(out["rows"]) == 3
    first = out["rows"][0]
    assert first["row"] == 4
    assert "A4" in first["cells"]


def test_load_range_as_df_with_header():
    _need_bench()
    df = load_range_as_df(
        BENCH_FILES / "synthetic1_A.xlsx",
        "Portfolio",
        "A4:N10",
        header_in_range=True,
    )
    assert isinstance(df, pd.DataFrame)
    assert "Company" in df.columns
    assert len(df) == 6  # 7 rows, first is header


# ---- Citation -----------------------------------------------------------


def test_format_citation_single_cell():
    out = format_citation("Apex has Entry EV $478M", "Portfolio", ["G6"])
    assert "[Portfolio!G6]" in out
    assert "Apex" in out


def test_format_citation_multiple_cells():
    out = format_citation("Total: $4.2B", "Portfolio", ["G6", "G14", "G27"])
    assert "[Portfolio!G6,G14,G27]" in out


def test_format_citation_empty_cells():
    out = format_citation("standalone claim", "Portfolio", [])
    assert out == "standalone claim"


# ---- LocalSandbox -------------------------------------------------------


def test_local_sandbox_requires_explicit_allow():
    with pytest.raises(RuntimeError, match="unsafe"):
        LocalSandbox()
    # Explicit allow works
    LocalSandbox(allow_unsafe=True)


def test_local_sandbox_basic_arithmetic():
    sb = LocalSandbox(allow_unsafe=True)
    result = sb.execute("__result__ = 2 + 2", dataframes={})
    assert result.error is None
    assert result.result == 4


def test_local_sandbox_with_dataframe():
    sb = LocalSandbox(allow_unsafe=True)
    df = pd.DataFrame({"Fund": ["I", "I", "II"], "EV": [100, 200, 300]})
    code = "__result__ = df.groupby('Fund')['EV'].sum().to_dict()"
    result = sb.execute(code, dataframes={"df": df})
    assert result.error is None
    assert result.result == {"I": 300, "II": 300}


def test_local_sandbox_captures_error():
    sb = LocalSandbox(allow_unsafe=True)
    result = sb.execute("__result__ = 1 / 0", dataframes={})
    assert result.error is not None
    assert "ZeroDivision" in result.error
    assert result.result is None


def test_local_sandbox_serializes_numpy_floats():
    sb = LocalSandbox(allow_unsafe=True)
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
    result = sb.execute("__result__ = df['x'].mean()", dataframes={"df": df})
    assert result.error is None
    # Should be a plain Python float, not numpy.float64
    assert isinstance(result.result, float)
    assert result.result == 2.0


def test_local_sandbox_handles_nan():
    sb = LocalSandbox(allow_unsafe=True)
    df = pd.DataFrame({"x": [float("nan"), 1.0, 2.0]})
    result = sb.execute("__result__ = df['x'].iloc[0]", dataframes={"df": df})
    assert result.error is None
    assert result.result is None  # NaN serialized as None


# ---- DockerSandbox (no real Docker call) --------------------------------


def test_docker_sandbox_requires_docker_on_path():
    """If `docker` isn't on PATH, construction raises a clear error."""
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="Docker not found"),
    ):
        DockerSandbox()


def test_docker_sandbox_run_args_include_security_flags():
    """Verify the security flags D12 documented are actually applied."""
    with patch("shutil.which", return_value="/usr/bin/docker"):
        sb = DockerSandbox(image="finsheet-sandbox:test")
        args = sb._docker_run_args(Path("/tmp/test"))
    # Critical flags
    assert "--rm" in args
    assert "--read-only" in args
    assert "--network=none" in args
    assert "--user=sandbox" in args
    assert "--memory=512m" in args
    assert "--cpus=1" in args
    # Tmpfs for Python's import machinery
    assert any("/tmp:size=" in a for a in args)
    # Data mounted read-only
    assert any(a.endswith(":/data:ro") for a in args)
    # Image is last
    assert args[-1] == "finsheet-sandbox:test"


def test_docker_sandbox_execute_invokes_subprocess(tmp_path):
    """Mock subprocess.run and verify the call shape."""
    fake_stdout = json.dumps(
        {
            "result": 42,
            "stdout": "",
            "stderr": "",
            "error": None,
        }
    )
    fake_proc = MagicMock(returncode=0, stdout=fake_stdout + "\n", stderr="")
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=fake_proc) as mock_run,
    ):
        sb = DockerSandbox()
        result = sb.execute("__result__ = 42", dataframes={})
    assert result.result == 42
    assert result.error is None
    # subprocess.run was called once with docker command
    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "docker"
    assert cmd[1] == "run"


def test_docker_sandbox_handles_timeout():
    import subprocess

    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=30)),
    ):
        sb = DockerSandbox()
        result = sb.execute("...", dataframes={}, timeout_s=30)
    assert result.error is not None
    assert "timeout" in result.error.lower()
    assert result.exit_code == 124


# ---- Tool functions (wrappers) ------------------------------------------


def test_tool_list_sheets_file_not_found():
    out = tool_list_sheets("/nonexistent/file.xlsx")
    assert "error" in out


def test_tool_list_sheets_success():
    _need_bench()
    out = tool_list_sheets(str(BENCH_FILES / "synthetic1_A.xlsx"))
    assert "sheets" in out
    assert "Portfolio" in out["sheets"]


def test_tool_get_sheet_schema_success():
    _need_bench()
    out = tool_get_sheet_schema(str(BENCH_FILES / "synthetic1_A.xlsx"))
    assert out["name"] == "Portfolio"
    assert out["fund_layout"] in ("column", "row_separator", "unknown")


def test_tool_get_range_invalid_range():
    _need_bench()
    out = tool_get_range(
        str(BENCH_FILES / "synthetic1_A.xlsx"),
        "Portfolio",
        "garbage",
    )
    assert "error" in out


def test_tool_execute_python_end_to_end():
    _need_bench()
    sandbox = LocalSandbox(allow_unsafe=True)
    out = tool_execute_python(
        file_path=str(BENCH_FILES / "synthetic1_A.xlsx"),
        code="__result__ = len(df)",
        named_ranges={"df": {"sheet": "Portfolio", "range": "A4:N50"}},
        sandbox=sandbox,
    )
    assert out["error"] is None
    # Range A4:N50 with header at A4 → 46 data rows (50 - 4)
    assert isinstance(out["result"], int)
    assert out["result"] > 0


def test_tool_execute_python_invalid_named_range():
    _need_bench()
    sandbox = LocalSandbox(allow_unsafe=True)
    out = tool_execute_python(
        file_path=str(BENCH_FILES / "synthetic1_A.xlsx"),
        code="__result__ = 1",
        named_ranges={"df": {"sheet": "Portfolio", "range": "not-a-range"}},
        sandbox=sandbox,
    )
    assert "error" in out
    assert "named ranges" in out["error"].lower() or "invalid" in out["error"].lower()


def test_tool_cite_cells():
    out = tool_cite_cells("Apex has Entry EV $478M", "Portfolio", ["G6"])
    assert "[Portfolio!G6]" in out


# ---- Factory ------------------------------------------------------------


def test_make_sandbox_local_requires_allow_unsafe():
    with pytest.raises(RuntimeError):
        make_sandbox(prefer="local_unsafe")
    sb = make_sandbox(prefer="local_unsafe", allow_unsafe=True)
    assert sb.name == "local_unsafe"


def test_make_sandbox_docker_raises_when_missing():
    with patch("shutil.which", return_value=None), pytest.raises(RuntimeError):
        make_sandbox(prefer="docker")


# ---- SandboxResult ------------------------------------------------------


def test_sandbox_result_to_dict():
    r = SandboxResult(result=42, stdout="hi", stderr="", error=None, exit_code=0)
    d = r.to_dict()
    assert d["result"] == 42
    assert d["error"] is None
