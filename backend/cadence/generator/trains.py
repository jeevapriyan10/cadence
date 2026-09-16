"""Train slot generation respecting network graph topology and profile headway rules."""

from datetime import datetime, timedelta, timezone
import random
from typing import Any, Optional

from cadence.domain.graph import NetworkGraph
from cadence.domain.models import SectionAdjacency, TrackSection, TrainSlot
from cadence.domain.schemas import TrainSlotSchema
from cadence.profiles.base import NetworkProfile

DEFAULT_BASE_TIME = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
DEFAULT_HOP_MINUTES = 3


def generate_train_slots(
    sections: list[TrackSection],
    profile: NetworkProfile,
    rng: random.Random,
    count: int,
    time_window_hours: int = 24,
    adjacencies: Optional[list[SectionAdjacency]] = None,
    express_fraction: float = 0.35,
    base_time: Optional[datetime] = None,
) -> list[TrainSlot]:
    """Generate train slots with contiguous routes and profile-compliant headway spacing.

    Args:
        sections: Track sections available in the network.
        profile: Active NetworkProfile defining headway and priority rules.
        rng: Seeded random number generator instance.
        count: Number of train slots to generate.
        time_window_hours: Planning horizon in hours.
        adjacencies: Optional section adjacencies used to build the graph.
        express_fraction: Fraction of slots tagged as express for LocalProfile.
        base_time: Deterministic reference datetime for scheduled starts.

    Returns:
        list[TrainSlot]: Scheduled train slots with randomized routes and compliant headway.
    """
    if not sections or count <= 0:
        return []

    ref_time = base_time if base_time is not None else DEFAULT_BASE_TIME
    total_minutes = time_window_hours * 60

    # Build network graph to discover contiguous paths
    if adjacencies is not None:
        graph = NetworkGraph.build_from_sections(sections, adjacencies)
    else:
        sequential_adjs = [
            SectionAdjacency(
                section_a_id=sections[i].id,
                section_b_id=sections[i + 1].id,
            )
            for i in range(len(sections) - 1)
        ]
        graph = NetworkGraph.build_from_sections(sections, sequential_adjs)

    section_ids = [s.id for s in sections]
    generated_slots: list[TrainSlot] = []
    # Track scheduled slots with schema and minute offsets: list of (slot, schema, start_minute, duration, route)
    scheduled_records: list[tuple[TrainSlot, TrainSlotSchema, int, int, list[str]]] = []

    window_step = max(5.0, (total_minutes - 120.0) / max(1, count))

    for i in range(count):
        # 1. Pick a contiguous route through the section graph
        route = _generate_contiguous_route(graph, section_ids, rng)
        hop_count = max(1, len(route) - 1)
        route_duration = max(5, hop_count * DEFAULT_HOP_MINUTES)

        # 2. Configure train type, naming, priority, and attributes by profile
        name, priority, attrs = _build_train_attributes(
            profile=profile,
            index=i,
            rng=rng,
            express_fraction=express_fraction,
        )

        # Build candidate schema to compute headway against existing slots
        dummy_time = ref_time
        cand_schema = TrainSlotSchema(
            id=f"TRAIN-{i + 1:03d}",
            name=name,
            scheduled_start=dummy_time,
            scheduled_end=dummy_time + timedelta(minutes=route_duration),
            route=route,
            priority=priority,
            attributes=attrs,
        )

        # 3. Find a start minute that satisfies headway for all overlapping routes
        base_target_minute = int(i * window_step + rng.uniform(0.0, max(1.0, window_step * 0.5)))
        candidate_minute = _find_conflict_free_start_minute(
            candidate_minute=base_target_minute,
            route=route,
            route_duration=route_duration,
            cand_schema=cand_schema,
            profile=profile,
            scheduled_records=scheduled_records,
            total_minutes=total_minutes,
        )

        # 4. Finalize TrainSlot model
        slot_start = ref_time + timedelta(minutes=candidate_minute)
        slot_end = slot_start + timedelta(minutes=route_duration)

        final_schema = TrainSlotSchema(
            id=cand_schema.id,
            name=name,
            scheduled_start=slot_start,
            scheduled_end=slot_end,
            route=route,
            priority=priority,
            attributes=attrs,
        )

        slot = TrainSlot(
            id=cand_schema.id,
            name=name,
            scheduled_start=slot_start,
            scheduled_end=slot_end,
            route=route,
            priority=priority,
            attributes=attrs,
        )

        generated_slots.append(slot)
        scheduled_records.append((slot, final_schema, candidate_minute, route_duration, route))

    return generated_slots


def _generate_contiguous_route(
    graph: NetworkGraph,
    section_ids: list[str],
    rng: random.Random,
) -> list[str]:
    """Find a contiguous path of track sections using shortest path or random walk."""
    if len(section_ids) == 1:
        return [section_ids[0]]

    # Try up to 10 random origin/destination pairs to find a shortest path
    for _ in range(10):
        origin = rng.choice(section_ids)
        dest = rng.choice(section_ids)
        if origin != dest:
            path = graph.shortest_path(origin, dest)
            if len(path) >= 2:
                # Limit path length to demo-scale (2 to 7 sections)
                if len(path) > 7:
                    max_start = len(path) - 7
                    start_idx = rng.randint(0, max_start)
                    path = path[start_idx : start_idx + 7]
                return path

    # Fallback to random walk if graph paths could not be sampled
    origin = rng.choice(section_ids)
    walk = [origin]
    curr = origin
    walk_len = rng.randint(2, min(5, len(section_ids)))
    for _ in range(walk_len):
        neighbors = graph.get_neighbors(curr)
        unvisited = [n for n in neighbors if n not in walk]
        next_sec = rng.choice(unvisited) if unvisited else (rng.choice(neighbors) if neighbors else None)
        if not next_sec:
            break
        walk.append(next_sec)
        curr = next_sec

    return walk if len(walk) >= 2 else section_ids[: min(3, len(section_ids))]


def _build_train_attributes(
    profile: NetworkProfile,
    index: int,
    rng: random.Random,
    express_fraction: float,
) -> tuple[str, int, dict[str, Any]]:
    """Generate appropriate names, priorities, and attributes based on NetworkProfile."""
    p_name = profile.profile_name.lower()

    if p_name == "local":
        is_express = rng.random() < express_fraction
        train_type = "express" if is_express else "local"
        priority = rng.randint(3, 5) if is_express else rng.randint(1, 3)
        name = f"Express Train {index + 1:02d}" if is_express else f"Local Train {index + 1:02d}"
        attrs: dict[str, Any] = {
            "train_type": train_type,
            "is_express": is_express,
            "is_local": not is_express,
            "service_type": train_type,
        }
    elif p_name == "mainline":
        is_freight = rng.random() < 0.35
        train_type = "freight" if is_freight else "passenger"
        priority = rng.randint(3, 5) if not is_freight else rng.randint(1, 3)
        name = f"Freight Trunk {index + 1:02d}" if is_freight else f"Intercity Passenger {index + 1:02d}"
        attrs = {
            "train_type": train_type,
            "is_freight": is_freight,
            "is_passenger": not is_freight,
            "service_type": train_type,
        }
    elif p_name == "metro":
        priority = rng.randint(1, 4)
        name = f"Metro Fleet {index + 1:02d}"
        attrs = {
            "train_type": "metro",
            "line": "Metro Loop",
        }
    else:
        priority = rng.randint(1, 3)
        name = f"Train {index + 1:02d}"
        attrs = {"train_type": "standard"}

    return name, priority, attrs


def _find_conflict_free_start_minute(
    candidate_minute: int,
    route: list[str],
    route_duration: int,
    cand_schema: TrainSlotSchema,
    profile: NetworkProfile,
    scheduled_records: list[tuple[TrainSlot, TrainSlotSchema, int, int, list[str]]],
    total_minutes: int,
) -> int:
    """Find a start minute that has no headway violations with overlapping scheduled slots."""
    cur_minute = max(0, min(candidate_minute, total_minutes - route_duration))
    route_set = set(route)
    max_search_minute = total_minutes - route_duration

    while cur_minute <= max_search_minute:
        has_conflict = False

        for _, other_schema, other_start_min, _, other_route in scheduled_records:
            shared = route_set & set(other_route)
            if not shared:
                continue

            required_headway = profile.min_headway_minutes(cand_schema, other_schema)

            # Check scheduled start time separation
            if abs(cur_minute - other_start_min) < required_headway:
                has_conflict = True
                cur_minute = other_start_min + required_headway
                break

            # Check arrival times on each shared section
            for sec in shared:
                cur_sec_time = cur_minute + route.index(sec) * DEFAULT_HOP_MINUTES
                other_sec_time = other_start_min + other_route.index(sec) * DEFAULT_HOP_MINUTES
                if abs(cur_sec_time - other_sec_time) < required_headway:
                    has_conflict = True
                    cur_minute += 1
                    break

            if has_conflict:
                break

        if not has_conflict:
            return cur_minute

    # If end of horizon is reached, return clamped candidate minute
    return max(0, min(candidate_minute, total_minutes - route_duration))
