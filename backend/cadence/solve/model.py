"""Core CP-SAT constraint programming model builder for Cadence block scheduling."""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional, Union

from ortools.sat.python import cp_model

from cadence.domain.graph import NetworkGraph
from cadence.domain.models import MaintenanceTask, SectionAdjacency, TrackSection, TrainSlot
from cadence.domain.schemas import (
    MaintenanceTaskSchema,
    SectionAdjacencySchema,
    TrackSectionSchema,
    TrainSlotSchema,
)
from cadence.profiles.base import NetworkProfile
from cadence.solve.safety import precompute_unsafe_adjacency_pairs

DEFAULT_SCALE_FACTOR = 100
DEFAULT_BASE_TIME = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


class BuildModelResult(dict):
    """Result dictionary returned by build_cp_model.

    Contains 'model', 'interval_vars', and 'unsafe_adjacency_pairs'.
    Also supports 2-tuple unpacking (model, interval_vars = build_cp_model(...))
    for complete backwards compatibility with earlier modules.
    """

    def __init__(
        self,
        model: cp_model.CpModel,
        interval_vars: dict[str, cp_model.IntervalVar],
        unsafe_adjacency_pairs: list[tuple[str, str, str]],
    ) -> None:
        super().__init__(
            model=model,
            interval_vars=interval_vars,
            unsafe_adjacency_pairs=unsafe_adjacency_pairs,
        )
        self.model = model
        self.interval_vars = interval_vars
        self.unsafe_adjacency_pairs = unsafe_adjacency_pairs

    def __iter__(self):
        # Enables backwards-compatible 2-tuple unpacking: model, interval_vars = build_cp_model(...)
        return iter([self.model, self.interval_vars])


def build_cp_model(
    sections: list[Union[TrackSectionSchema, TrackSection]],
    tasks: list[Union[MaintenanceTaskSchema, MaintenanceTask]],
    train_slots: list[Union[TrainSlotSchema, TrainSlot]],
    profile: NetworkProfile,
    time_horizon_minutes: int,
    base_time: Optional[datetime] = None,
    graph: Optional[NetworkGraph] = None,
    adjacencies: Optional[list[Union[SectionAdjacencySchema, SectionAdjacency]]] = None,
) -> BuildModelResult:
    """Build a CP-SAT constraint programming model for maintenance block scheduling.

    Args:
        sections: Track sections in the railway network.
        tasks: Maintenance tasks to be scheduled.
        train_slots: Scheduled train slots traversing the network.
        profile: Active NetworkProfile defining priority weights and disruption penalties.
        time_horizon_minutes: Maximum scheduling horizon in integer minutes.
        base_time: Optional reference datetime representing minute 0. If None, derived
            from the earliest task or train slot start.
        graph: Optional NetworkGraph representing the network topology. If None, built from
            sections and adjacencies.
        adjacencies: Optional list of SectionAdjacency connections between track sections.

    Returns:
        BuildModelResult: Dictionary holding:
            - 'model': Configured CpModel instance.
            - 'interval_vars': Mapping of task_id to its corresponding CP-SAT IntervalVar.
            - 'unsafe_adjacency_pairs': List of (section_a, section_b, reason) flagged unsafe.
    """
    model = cp_model.CpModel()
    interval_vars: dict[str, cp_model.IntervalVar] = {}

    # Build or resolve NetworkGraph for safety-adjacency precomputation
    if graph is not None:
        net_graph = graph
    elif adjacencies is not None:
        net_graph = NetworkGraph.build_from_sections(sections, adjacencies)
    else:
        net_graph = NetworkGraph.build_from_sections(sections, [])

    # Precompute unsafe adjacency pairs before building interval variables
    unsafe_adjacency_pairs = precompute_unsafe_adjacency_pairs(
        graph=net_graph,
        profile=profile,
        sections=sections,
        train_slots=train_slots,
    )

    if not tasks:
        return BuildModelResult(
            model=model,
            interval_vars=interval_vars,
            unsafe_adjacency_pairs=unsafe_adjacency_pairs,
        )

    # Determine reference base datetime for integer minute conversion
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

    # Track start/end integer variables and tasks by section
    tasks_by_section: dict[str, list[cp_model.IntervalVar]] = defaultdict(list)
    task_schema_map: dict[str, MaintenanceTaskSchema] = {}
    task_start_vars: dict[str, cp_model.IntVar] = {}
    task_end_vars: dict[str, cp_model.IntVar] = {}
    task_es_offsets: dict[str, int] = {}

    # 1. Create CP-SAT IntervalVars and bounds for each MaintenanceTask
    for task in tasks:
        task_schema = (
            task if isinstance(task, MaintenanceTaskSchema) else MaintenanceTaskSchema.model_validate(task)
        )
        task_schema_map[task_schema.id] = task_schema

        duration = max(1, int(task_schema.duration_minutes))

        # Convert datetimes to relative integer minutes from horizon base_time
        es_offset = max(0, int((task_schema.earliest_start - ref_time).total_seconds() // 60))
        le_offset = min(time_horizon_minutes, int((task_schema.latest_end - ref_time).total_seconds() // 60))

        start_var = model.NewIntVar(0, time_horizon_minutes, f"start_{task_schema.id}")
        end_var = model.NewIntVar(0, time_horizon_minutes, f"end_{task_schema.id}")
        interval_var = model.NewIntervalVar(
            start_var,
            duration,
            end_var,
            f"interval_{task_schema.id}",
        )

        # Enforce execution window [earliest_start, latest_end]
        model.Add(start_var >= es_offset)
        model.Add(end_var <= le_offset)

        interval_vars[task_schema.id] = interval_var
        task_start_vars[task_schema.id] = start_var
        task_end_vars[task_schema.id] = end_var
        task_es_offsets[task_schema.id] = es_offset
        tasks_by_section[task_schema.section_id].append(interval_var)

    # 2. AddNoOverlap for tasks sharing the same track section
    for section_id, section_intervals in tasks_by_section.items():
        if len(section_intervals) > 1:
            model.AddNoOverlap(section_intervals)

    # =========================================================================
    # SAFETY-ADJACENCY HARD CONSTRAINTS (CREDIBILITY-CRITICAL)
    # -------------------------------------------------------------------------
    # STRUCTURAL HARD CONSTRAINT: This constraint is credibility-critical and
    # must NEVER be made configurable, tunable, bypassable, or reducible to a
    # soft penalty. If two adjacent track sections cannot be safely blocked
    # simultaneously under the active profile's is_safe_adjacency() check,
    # any maintenance block on section_a and any maintenance block on section_b
    # are strictly forbidden from overlapping in time.
    # =========================================================================
    for sec_a_id, sec_b_id, reason in unsafe_adjacency_pairs:
        tasks_on_a = tasks_by_section.get(sec_a_id, [])
        tasks_on_b = tasks_by_section.get(sec_b_id, [])

        for iv_a in tasks_on_a:
            for iv_b in tasks_on_b:
                task_a_id = iv_a.Name().replace("interval_", "")
                task_b_id = iv_b.Name().replace("interval_", "")

                start_a = task_start_vars[task_a_id]
                end_a = task_end_vars[task_a_id]
                start_b = task_start_vars[task_b_id]
                end_b = task_end_vars[task_b_id]

                before_ab = model.NewBoolVar(f"safety_{task_a_id}_before_{task_b_id}")
                before_ba = model.NewBoolVar(f"safety_{task_b_id}_before_{task_a_id}")

                model.Add(end_a <= start_b).OnlyEnforceIf(before_ab)
                model.Add(end_b <= start_a).OnlyEnforceIf(before_ba)
                model.AddBoolOr([before_ab, before_ba])

                # Disjunctive non-overlap between intervals
                model.AddNoOverlap([iv_a, iv_b])

    # 3. Soft Objective:
    # Term A: Minimize sum of (priority_weight * delay_from_earliest)
    # Term B: Minimize sum of (disruption_penalty * is_overlap)
    objective_terms: list[Any] = []

    for task_id, task_schema in task_schema_map.items():
        start_var = task_start_vars[task_id]
        es_offset = task_es_offsets[task_id]

        weight = profile.priority_weight(task_schema)
        scaled_weight = max(1, int(round(weight * DEFAULT_SCALE_FACTOR)))

        delay_var = model.NewIntVar(0, time_horizon_minutes, f"delay_{task_id}")
        model.Add(delay_var == start_var - es_offset)
        objective_terms.append(scaled_weight * delay_var)

    # Build disruption penalties for train slots traversing blocked sections
    for slot in train_slots:
        slot_schema = (
            slot if isinstance(slot, TrainSlotSchema) else TrainSlotSchema.model_validate(slot)
        )
        slot_route_set = set(slot_schema.route)
        penalty = profile.disruption_penalty(slot_schema)
        scaled_penalty = max(1, int(round(penalty * DEFAULT_SCALE_FACTOR)))

        slot_start_min = max(0, int((slot_schema.scheduled_start - ref_time).total_seconds() // 60))
        slot_end_min = min(time_horizon_minutes, int((slot_schema.scheduled_end - ref_time).total_seconds() // 60))

        # Check all tasks running on a section traversed by this train slot
        for task_id, task_schema in task_schema_map.items():
            if task_schema.section_id not in slot_route_set:
                continue

            start_var = task_start_vars[task_id]
            end_var = task_end_vars[task_id]

            # Reify interval overlap: overlap <==> NOT (end <= slot_start OR start >= slot_end)
            before = model.NewBoolVar(f"before_{task_id}_{slot_schema.id}")
            after = model.NewBoolVar(f"after_{task_id}_{slot_schema.id}")

            model.Add(end_var <= slot_start_min).OnlyEnforceIf(before)
            model.Add(end_var > slot_start_min).OnlyEnforceIf(before.Not())

            model.Add(start_var >= slot_end_min).OnlyEnforceIf(after)
            model.Add(start_var < slot_end_min).OnlyEnforceIf(after.Not())

            is_overlap = model.NewBoolVar(f"overlap_{task_id}_{slot_schema.id}")
            model.AddBoolAnd([before.Not(), after.Not()]).OnlyEnforceIf(is_overlap)
            model.AddBoolOr([before, after]).OnlyEnforceIf(is_overlap.Not())

            objective_terms.append(scaled_penalty * is_overlap)

    if objective_terms:
        model.Minimize(sum(objective_terms))

    return BuildModelResult(
        model=model,
        interval_vars=interval_vars,
        unsafe_adjacency_pairs=unsafe_adjacency_pairs,
    )

