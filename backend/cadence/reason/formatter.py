"""Formatting and serialization helpers for post-hoc scheduling explanations."""

from typing import Any

from cadence.reason.explainer import Explanation


def format_explanation(explanation: Explanation) -> str:
    """Format an Explanation instance into a clear, readable multi-line string.

    Example Outputs:
        Task Track Resurfacing (T1) scheduled at 06:00 - 07:00 — Scheduled at earliest possible start time (06:00)...
        Rejected alternative slots:
          • 06:15 - 07:15: Section 'S1' is occupied by scheduled maintenance task 'T2'. [conflict: task T2]
          • 06:30 - 07:30: Unsafe: blocking sections ['S1', 'S2'] leaves only a single isolated track section. [conflict: safety_adjacency S2]

    Args:
        explanation: Explanation instance.

    Returns:
        str: Cleanly formatted multi-line string.
    """
    time_str = f"{explanation.scheduled_start.strftime('%H:%M')} - {explanation.scheduled_end.strftime('%H:%M')}"
    header = f"Task {explanation.task_name} ({explanation.task_id}) scheduled at {time_str} — {explanation.chosen_reason}"

    if not explanation.rejected_candidates:
        return f"{header}\n  No alternative candidate slots were rejected."

    lines = [header, "  Rejected alternative candidate slots:"]
    for cand in explanation.rejected_candidates:
        c_time = f"{cand.candidate_start.strftime('%H:%M')} - {cand.candidate_end.strftime('%H:%M')}"
        lines.append(
            f"    • {c_time} rejected: {cand.rejection_reason} "
            f"(conflict: {cand.conflicting_entity_type} {cand.conflicting_entity_id})"
        )

    return "\n".join(lines)


def explanation_to_dict(explanation: Explanation) -> dict[str, Any]:
    """Serialize an Explanation into a pure JSON-compatible Python dictionary.

    Args:
        explanation: Explanation instance.

    Returns:
        dict[str, Any]: Dictionary with ISO-8601 formatted datetime strings and primitive types.
    """
    return explanation.model_dump(mode="json")
