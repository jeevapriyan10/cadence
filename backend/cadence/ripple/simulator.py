"""Cascade delay-propagation simulator (Ripple) for railway networks."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from cadence.domain.graph import NetworkGraph
from cadence.domain.models import utc_now
from cadence.domain.schemas import (
    ScheduledBlockSchema,
    TrackSectionSchema,
    TrainSlotSchema,
)


class TrainImpact(BaseModel):
    """Impact analysis details for a single train slot affected directly or indirectly."""

    train_slot_id: str
    train_name: str
    directly_affected: bool
    delay_minutes: float = Field(ge=0.0)
    affected_sections: list[str] = Field(default_factory=list)
    cascade_source: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RippleReport(BaseModel):
    """Overall cascade delay-propagation simulation report for a solved schedule."""

    total_trains_affected: int = Field(ge=0)
    total_delay_minutes: float = Field(ge=0.0)
    per_train_impacts: list[TrainImpact] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _compute_train_section_windows(
    train_slot: TrainSlotSchema,
) -> list[tuple[str, datetime, datetime]]:
    """Compute estimated arrival and departure timestamps for each section along a train's route.

    Simplification: If no finer-grained per-section timing exists in train_slot attributes,
    the train's scheduled duration is distributed evenly across all legs in its ordered route.

    Args:
        train_slot: TrainSlotSchema with scheduled_start, scheduled_end, and ordered route.

    Returns:
        list[tuple[str, datetime, datetime]]: List of (section_id, arrival_time, departure_time).
    """
    route = train_slot.route
    if not route:
        return []

    total_duration = train_slot.scheduled_end - train_slot.scheduled_start
    leg_count = len(route)

    # If single section, train occupies it for the full duration
    if leg_count == 1:
        return [(route[0], train_slot.scheduled_start, train_slot.scheduled_end)]

    leg_seconds = total_duration.total_seconds() / leg_count
    leg_delta = timedelta(seconds=leg_seconds)

    windows: list[tuple[str, datetime, datetime]] = []
    for i, sec_id in enumerate(route):
        start_time = train_slot.scheduled_start + i * leg_delta
        end_time = train_slot.scheduled_start + (i + 1) * leg_delta
        windows.append((sec_id, start_time, end_time))

    return windows


def simulate_cascade(
    scheduled_blocks: list[Union[ScheduledBlockSchema, Any]],
    train_slots: list[Union[TrainSlotSchema, Any]],
    graph: Optional[NetworkGraph] = None,
    sections: Optional[list[Union[TrackSectionSchema, Any]]] = None,
) -> RippleReport:
    """Simulate downstream cascade delay propagation resulting from maintenance block possessions.

    Evaluation Workflow:
        1. Direct Impact Pass:
           Walks each train slot's route. Approximates arrival and departure windows per section
           using uniform temporal distribution across route legs. If any scheduled possession block
           overlaps with the train's occupancy on that section, marks directly_affected=True, computes
           the delay as the overlap duration, and logs the affected sections.

        2. Cascade Propagation Pass (Single-Hop Sequential Cascade):
           For trains not directly affected by maintenance possessions, checks if a train scheduled
           to use the same track section shortly after a directly-affected train is delayed enough
           to conflict with this train's scheduled presence on that section. Attributes cascade_source
           to the directly-delayed train.
           (Note: Multi-hop N-tier cascade chains are documented as out of scope for this module).

        3. Aggregation:
           Calculates total affected train count, total cumulative delay minutes, and packages
           per-train impact records into a RippleReport.

    Args:
        scheduled_blocks: Solved ScheduledBlockSchema possession blocks.
        train_slots: TrainSlotSchema timetable records.
        graph: Optional NetworkGraph of the track topology (for path/distance verification).
        sections: Optional list of TrackSectionSchema track sections in the network.

    Returns:
        RippleReport: Comprehensive delay impact summary and individual train impact items.
    """
    # Index scheduled blocks by section_id for O(1) section lookup
    blocks_by_section: dict[str, list[ScheduledBlockSchema]] = defaultdict(list)
    for b in scheduled_blocks:
        block_obj = b if isinstance(b, ScheduledBlockSchema) else ScheduledBlockSchema.model_validate(b)
        blocks_by_section[block_obj.section_id].append(block_obj)

    # Standardize train slots as schemas
    train_schema_map: dict[str, TrainSlotSchema] = {}
    train_windows_map: dict[str, list[tuple[str, datetime, datetime]]] = {}

    for slot in train_slots:
        slot_schema = slot if isinstance(slot, TrainSlotSchema) else TrainSlotSchema.model_validate(slot)
        train_schema_map[slot_schema.id] = slot_schema
        train_windows_map[slot_schema.id] = _compute_train_section_windows(slot_schema)

    directly_affected_impacts: dict[str, TrainImpact] = {}
    train_effective_delays: dict[str, float] = {}

    # -------------------------------------------------------------------------
    # Pass 1: Direct Impact Pass
    # -------------------------------------------------------------------------
    for slot_id, slot_schema in train_schema_map.items():
        windows = train_windows_map[slot_id]
        total_overlap_sec = 0.0
        affected_secs: list[str] = []

        for sec_id, t_start, t_end in windows:
            blocks = blocks_by_section.get(sec_id, [])
            for block in blocks:
                # Check for temporal overlap between train window and maintenance block
                overlap_start = max(t_start, block.start_time)
                overlap_end = min(t_end, block.end_time)

                if overlap_start < overlap_end:
                    overlap_sec = (overlap_end - overlap_start).total_seconds()
                    total_overlap_sec += overlap_sec
                    if sec_id not in affected_secs:
                        affected_secs.append(sec_id)

        if total_overlap_sec > 0:
            delay_min = round(total_overlap_sec / 60.0, 2)
            directly_affected_impacts[slot_id] = TrainImpact(
                train_slot_id=slot_id,
                train_name=slot_schema.name,
                directly_affected=True,
                delay_minutes=delay_min,
                affected_sections=affected_secs,
                cascade_source=None,
            )
            train_effective_delays[slot_id] = delay_min

    # -------------------------------------------------------------------------
    # Pass 2: Cascade Propagation Pass (Single-Hop Sequential Cascade)
    # -------------------------------------------------------------------------
    # For trains not directly affected, check if they share a section with a
    # directly-affected train whose pushed exit time now overlaps this train's arrival.
    cascade_impacts: dict[str, TrainImpact] = {}

    for slot_id, slot_schema in train_schema_map.items():
        if slot_id in directly_affected_impacts:
            continue

        candidate_cascades: list[tuple[float, str, str]] = []  # (delay, section_id, source_train_name)
        target_windows = train_windows_map[slot_id]

        for sec_id, b_start, b_end in target_windows:
            # Check all directly-affected trains that also use sec_id
            for delayed_id, delayed_impact in directly_affected_impacts.items():
                delayed_schema = train_schema_map[delayed_id]
                delayed_windows = train_windows_map[delayed_id]

                # Find window of delayed train on this same section
                delayed_sec_window = next((w for w in delayed_windows if w[0] == sec_id), None)
                if not delayed_sec_window:
                    continue

                _, a_orig_start, a_orig_end = delayed_sec_window
                delay_delta = timedelta(minutes=delayed_impact.delay_minutes)
                a_delayed_end = a_orig_end + delay_delta
                a_delayed_start = a_orig_start + delay_delta

                # If train B is scheduled to arrive during or shortly after train A,
                # check if train A's delayed departure encroaches upon train B's window
                if a_delayed_end > b_start and a_delayed_start < b_end:
                    # Encroachment overlap duration
                    cascade_overlap_start = max(b_start, a_delayed_start)
                    cascade_overlap_end = min(b_end, a_delayed_end)
                    overlap_sec = (cascade_overlap_end - cascade_overlap_start).total_seconds()
                    if overlap_sec > 0:
                        delay_min = round(overlap_sec / 60.0, 2)
                        candidate_cascades.append((delay_min, sec_id, delayed_schema.name))

        if candidate_cascades:
            # Take the worst cascade collision
            candidate_cascades.sort(key=lambda x: x[0], reverse=True)
            max_delay, cascade_sec, source_name = candidate_cascades[0]

            cascade_impacts[slot_id] = TrainImpact(
                train_slot_id=slot_id,
                train_name=slot_schema.name,
                directly_affected=False,
                delay_minutes=max_delay,
                affected_sections=[cascade_sec],
                cascade_source=source_name,
            )

    # -------------------------------------------------------------------------
    # Pass 3: Aggregation into RippleReport
    # -------------------------------------------------------------------------
    all_impacts = list(directly_affected_impacts.values()) + list(cascade_impacts.values())
    all_impacts.sort(key=lambda imp: (imp.delay_minutes, imp.train_name), reverse=True)

    total_trains = len(all_impacts)
    total_delay = round(sum(imp.delay_minutes for imp in all_impacts), 2)

    return RippleReport(
        total_trains_affected=total_trains,
        total_delay_minutes=total_delay,
        per_train_impacts=all_impacts,
        generated_at=utc_now(),
    )
