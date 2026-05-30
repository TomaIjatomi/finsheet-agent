"""Baselines for FinSheet Agent evaluation.

M1.3: FullContextSolver (Gemini 3.1 Pro, whole-spreadsheet-in-context)
M1.4: NaiveRagSolver (chunked retrieval)
M2.*: agent-based solvers will live alongside these and share the runner.
"""

from .runner import run_baseline
from .solver import FullContextSolver, Solver, SolveResult

__all__ = ["Solver", "SolveResult", "FullContextSolver", "run_baseline"]
