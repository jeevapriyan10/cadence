"""Solve orchestration and result models for CP-SAT block scheduling."""

from datetime import datetime, timedelta, timezone
import time
from typing import Optional, Union

from ortools.sat.python import cp_model
from pydantic import BaseModel, ConfigDict, Field

from cadence.domain.models import MaintenanceTask, TrackSection, TrainSlot
from cadence.domain.schemas import (
    MaintenanceTaskSchema,
    ScheduledBlockSchema,
    TrackSectionSchema,
    TrainSlotSchema,
)
from cadence.profiles.base import NetworkProfile
from cadence.solve.model import DEFAULT_BASE_TIME, DEFAULT_SCALE_FACTOR, build_cp_model


class SolveResult(BaseModel):
    """Result of solving a railway maintenance block scheduling scenario."""

    status: str
    scheduled_blocks: list[ScheduledBlockSchema] = Field(default_factory=list)
    objective_value: Optional[float] = None
    wall_time_seconds: float = 0.0

    model_config = ConfigDict(arbitrary_types_allowed=True)


def solve_schedule(
    sections: list[Union[TrackSectionSchema, TrackSection]],
    tasks: list[Union[MaintenanceTaskSchema, MaintenanceTask]],
    train_slots: list[Union[TrainSlotSchema, TrainSlot]],
    profile: NetworkProfile,
    time_horizon_minutes: int = 1440,
    time_limit_seconds: int = 30,
    base_time: Optional[datetime] = None,
) -> SolveResult:
    """Solve the maintenance possession block scheduling problem using Google OR-Tools CP-SAT.

    Args:
        sections: Track sections in the railway network.
        tasks: Maintenance possession tasks to be scheduled.
        train_slots: Scheduled train services traversing the network.
        profile: Active NetworkProfile defining priority rules and disruption penalties.
        time_horizon_minutes: Maximum scheduling horizon in integer minutes (default 24h = 1440).
        time_limit_seconds: Maximum wall time allowed for the CP-SAT solver.
        base_time: Reference datetime for minute 0. If None, inferred from task/slot timestamps.

    Returns:
        SolveResult: Solver status, concrete ScheduledBlockSchema records, objective, and duration.
    """
    if not tasks:
        return SolveResult(
            status="OPTIMAL",
            scheduled_blocks=[],
            objective_value=0.0,
            wall_time_seconds=0.001,
        )

    # Determine reference base time
    if base_time is None:
        earliest_task_dt = min(
            (t.earliest_start for t in tasks if hasattr(t, "earliest_start") and t.earliest_start),
            default=None,
        )
        earliest_train_dt = min(
            (s.scheduled_start for s in train_slots if hasattr(s, "scheduled_start") and s.scheduled_start),
            default=None,
        )
        candidates = [dt for dt in (earliest_task_dt, earliest_train_dt) if dt is not None]
        ref_time = min(candidates) if candidates else DEFAULT_BASE_TIME
    else:
        ref_time = base_time

    # Build CP-SAT model and interval variable mapping
    model, interval_vars = build_cp_model(
        sections=sections,
        tasks=tasks,
        train_slots=train_slots,
        profile=profile,
        time_horizon_minutes=time_horizon_minutes,
        base_time=ref_time,
    )

    # Configure solver parameters
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)

    t0 = time.perf_counter()
    status_code = solver.Solve(model)
    t1 = time.perf_counter()

    measured_wall_time = t1 - t0
    solver_wall_time = solver.WallTime()
    wall_time_seconds = max(0.001, solver_wall_time if solver_wall_time > 0.0 else measured_wall_time)

    # Map CP-SAT status to standard status strings
    status_map = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "INFEASIBLE",
        cp_model.UNKNOWN: "UNKNOWN",
    }
    status_str = status_map.get(status_code, "UNKNOWN")

    # If solved to OPTIMAL or FEASIBLE, extract scheduled blocks
    scheduled_blocks: list[ScheduledBlockSchema] = []
    objective_value: Optional[float] = None

    if status_str in ("OPTIMAL", "FEASIBLE"):
        task_lookup = {
            (t.id if hasattr(t, "id") else t["id"]): t for t in tasks
        }

        for task_id, iv in interval_vars.items():
            start_min = solver.Value(iv.StartExpr())
            end_min = solver.Value(iv.EndExpr())

            start_dt = ref_time + timedelta(minutes=start_min)
            end_dt = ref_time + timedelta(minutes=end_min)

            task_obj = task_lookup[task_id]
            section_id = task_obj.section_id if hasattr(task_obj, "section_id") else task_obj["section_id"]

            block = ScheduledBlockSchema(
                task_id=task_id,
                section_id=section_id,
                start_time=start_dt,
                end_time=end_dt,
                method="full_resolve",
            )
            scheduled_blocks.append(block)

        # Sort blocks by start time for consistent readability
        scheduled_blocks.sort(key=lambda b: (b.start_time, b.section_id, b.task_id))
        objective_value = float(solver.ObjectiveValue()) / float(DEFAULT_SCALE_FACTOR)

    return SolveResult(
        status=status_str,
        scheduled_blocks=scheduled_blocks,
        objective_value=objective_value,
        wall_time_seconds=wall_time_seconds,
    )
