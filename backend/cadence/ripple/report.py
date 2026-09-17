"""Reporting and serialization helpers for Ripple cascade delay reports."""

from typing import Any

from cadence.domain.models import utc_now
from cadence.ripple.simulator import RippleReport


def format_report_summary(report: RippleReport) -> str:
    """Format a concise, human-readable summary string of a Ripple cascade delay simulation.

    Example Outputs:
        - "3 trains affected, 47.0 total delay-minutes, worst-hit: Express-104 (22.0 min)"
        - "0 trains affected, 0.0 total delay-minutes, no disruptions"

    Args:
        report: RippleReport instance.

    Returns:
        str: Human-readable summary string suitable for CLI logs and dashboard cards.
    """
    if report.total_trains_affected == 0 or not report.per_train_impacts:
        return "0 trains affected, 0.0 total delay-minutes, no disruptions"

    worst_hit = max(report.per_train_impacts, key=lambda x: x.delay_minutes)
    worst_delay = round(worst_hit.delay_minutes, 1)
    total_delay = round(report.total_delay_minutes, 1)

    return (
        f"{report.total_trains_affected} trains affected, "
        f"{total_delay} total delay-minutes, "
        f"worst-hit: {worst_hit.train_name} ({worst_delay} min)"
    )


def report_to_dict(report: RippleReport) -> dict[str, Any]:
    """Convert a RippleReport instance into a JSON-serializable dictionary.

    Args:
        report: RippleReport instance.

    Returns:
        dict[str, Any]: Pure Python dictionary containing primitive types and ISO datetime strings.
    """
    return report.model_dump(mode="json")
