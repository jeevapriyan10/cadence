"""CP-SAT scheduling solver package for Cadence railway block scheduling."""

from cadence.solve.model import build_cp_model
from cadence.solve.safety import precompute_unsafe_adjacency_pairs
from cadence.solve.solver import SolveResult, solve_schedule

__all__ = [
    "build_cp_model",
    "precompute_unsafe_adjacency_pairs",
    "solve_schedule",
    "SolveResult",
]

