"""Reason: Post-hoc explainability layer for Cadence railway block scheduling."""

from cadence.reason.explainer import (
    Explanation,
    RejectedCandidate,
    check_candidate_feasibility,
    explain_full_schedule,
    explain_task_scheduling,
    generate_candidate_slots,
)
from cadence.reason.formatter import explanation_to_dict, format_explanation

__all__ = [
    "check_candidate_feasibility",
    "explain_full_schedule",
    "explain_task_scheduling",
    "Explanation",
    "explanation_to_dict",
    "format_explanation",
    "generate_candidate_slots",
    "RejectedCandidate",
]
