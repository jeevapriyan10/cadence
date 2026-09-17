"""Ripple: Cascade delay-propagation simulator for Cadence railway block scheduling."""

from cadence.ripple.report import format_report_summary, report_to_dict
from cadence.ripple.simulator import RippleReport, TrainImpact, simulate_cascade

__all__ = [
    "format_report_summary",
    "report_to_dict",
    "RippleReport",
    "simulate_cascade",
    "TrainImpact",
]
