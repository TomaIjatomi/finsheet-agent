"""FinSheet agent stack.

M2.2 — Schema Agent + Query Decomposition (done).
M2.3 — Computation Agent + Fact Sheet (here).
M2.4 — Verification Agent (next).
M2.5 — Tracing.

Public API:
    SchemaAgent          — deterministic; builds a SchemaCard
    DecompositionAgent   — Gemini 2.5 Pro; builds a QueryPlan
    ComputationAgent     — Gemini 2.5 Pro + MCP execute_python; builds a FactSheet
    build_prelude        — deterministic prelude builder (Fund column + avg-row filter)

Types (Pydantic):
    SchemaCard, ColumnSummary, FundSummary
    QueryPlan, Subgoal
    FactSheet, FactSheetEntry, ComputationResult, AgentResult
"""

from .agent_solver import AgentSolver
from .computation import ComputationAgent
from .decomposition import DecompositionAgent
from .prelude import build_prelude
from .schema import SchemaAgent
from .types import (
    AgentResult,
    ColumnSummary,
    ComputationResult,
    FactSheet,
    FactSheetEntry,
    FundSummary,
    QueryPlan,
    SchemaCard,
    Subgoal,
)

__all__ = [
    # agents
    "SchemaAgent",
    "DecompositionAgent",
    "ComputationAgent",
    "AgentSolver",
    "build_prelude",
    # types
    "SchemaCard",
    "ColumnSummary",
    "FundSummary",
    "QueryPlan",
    "Subgoal",
    "FactSheet",
    "FactSheetEntry",
    "ComputationResult",
    "AgentResult",
]
