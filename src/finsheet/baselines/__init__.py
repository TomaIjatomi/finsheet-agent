"""Baselines for FinSheet Agent evaluation.

M1.3: FullContextSolver (Gemini 2.5 Pro, whole-spreadsheet-in-context)
M1.4: NaiveRagSolver (chunked retrieval + top-K + Gemini 2.5 Pro)
M2.*: agent-based solvers will live alongside these and share the runner.
"""

from .naive_rag_solver import NaiveRagSolver
from .runner import run_baseline
from .solver import FullContextSolver, Solver, SolveResult

__all__ = [
    "Solver",
    "SolveResult",
    "FullContextSolver",
    "NaiveRagSolver",
    "run_baseline",
]
