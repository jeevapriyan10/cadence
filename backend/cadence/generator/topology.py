"""Topology generation functions for Cadence synthetic railway networks."""

import random
from typing import Any, Optional

from cadence.domain.models import SectionAdjacency, TrackSection


def generate_loop_topology(
    section_count: int,
    rng: random.Random,
) -> tuple[list[TrackSection], list[SectionAdjacency]]:
    """Build a loop topology where track sections are connected in a continuous ring.

    Args:
        section_count: Number of track sections in the ring (clamped to min 3).
        rng: Seeded random number generator instance.

    Returns:
        tuple[list[TrackSection], list[SectionAdjacency]]: Generated sections and ring edges.
    """
    count = max(3, section_count)
    sections: list[TrackSection] = []
    for i in range(count):
        length = round(rng.uniform(800.0, 2200.0), 1)
        sections.append(
            TrackSection(
                id=f"SEC-{i + 1:02d}",
                name=f"Metro Loop Section {i + 1}",
                length_meters=length,
                attributes={"topology": "loop", "index": i},
            )
        )

    adjacencies: list[SectionAdjacency] = []
    for i in range(count):
        next_idx = (i + 1) % count
        adjacencies.append(
            SectionAdjacency(
                id=f"ADJ-{i + 1:02d}",
                section_a_id=sections[i].id,
                section_b_id=sections[next_idx].id,
            )
        )

    return sections, adjacencies


def generate_linear_topology(
    section_count: int,
    rng: random.Random,
    has_express_stops: bool = True,
) -> tuple[list[TrackSection], list[SectionAdjacency]]:
    """Build a linear chain topology of track sections.

    Args:
        section_count: Number of track sections in the chain (clamped to min 2).
        rng: Seeded random number generator instance.
        has_express_stops: Whether to annotate sections with express/local tags.

    Returns:
        tuple[list[TrackSection], list[SectionAdjacency]]: Generated sections and chain edges.
    """
    count = max(2, section_count)
    sections: list[TrackSection] = []

    # Choose at least one express station if has_express_stops is True
    express_indices: set[int] = set()
    if has_express_stops and count >= 2:
        # Mark ~30% of sections as serving express and local
        for i in range(count):
            if rng.random() < 0.35:
                express_indices.add(i)
        # Guarantee at least one section has express tags
        if not express_indices:
            express_indices.add(count // 2)

    for i in range(count):
        length = round(rng.uniform(1000.0, 3000.0), 1)
        is_mixed = i in express_indices
        attrs: dict[str, Any] = {
            "topology": "linear",
            "index": i,
            "has_express_and_local": is_mixed,
            "train_types": ["express", "local"] if is_mixed else ["local"],
            "stop_type": "express_and_local" if is_mixed else "local_only",
        }
        sec_name = f"Station {i + 1} (Express/Local)" if is_mixed else f"Local Section {i + 1}"
        sections.append(
            TrackSection(
                id=f"SEC-{i + 1:02d}",
                name=sec_name,
                length_meters=length,
                attributes=attrs,
            )
        )

    adjacencies: list[SectionAdjacency] = []
    for i in range(count - 1):
        adjacencies.append(
            SectionAdjacency(
                id=f"ADJ-{i + 1:02d}",
                section_a_id=sections[i].id,
                section_b_id=sections[i + 1].id,
            )
        )

    return sections, adjacencies


def generate_linear_with_passing_loops_topology(
    section_count: int,
    rng: random.Random,
    loop_frequency: float = 0.2,
) -> tuple[list[TrackSection], list[SectionAdjacency]]:
    """Build a linear chain where roughly loop_frequency fraction of sections have a parallel passing loop.

    Args:
        section_count: Desired section count baseline (clamped to min 4).
        rng: Seeded random number generator instance.
        loop_frequency: Fraction of inner sections that receive an alternate passing loop.
            Accepts both fractions (e.g. 0.2) and interval frequencies (e.g. 4 -> 1 in 4 = 0.25).

    Returns:
        tuple[list[TrackSection], list[SectionAdjacency]]: All sections (mainline + passing loops)
            and all adjacency edges.
    """
    freq = (1.0 / loop_frequency) if loop_frequency > 1.0 else loop_frequency
    freq = max(0.05, min(1.0, freq))

    count = max(4, section_count)
    target_loop_count = max(1, int(round(count * freq / (1.0 + freq))))
    backbone_count = max(3, count - target_loop_count)
    sections: list[TrackSection] = []

    for i in range(backbone_count):
        length = round(rng.uniform(1200.0, 3500.0), 1)
        traffic_type = "passenger" if rng.random() < 0.65 else "freight"
        sections.append(
            TrackSection(
                id=f"SEC-{i + 1:02d}",
                name=f"Mainline Section {i + 1}",
                length_meters=length,
                attributes={
                    "topology": "linear_with_passing_loops",
                    "track_type": "mainline",
                    "traffic_type": traffic_type,
                    "index": i,
                },
            )
        )

    adjacencies: list[SectionAdjacency] = []
    for i in range(backbone_count - 1):
        adjacencies.append(
            SectionAdjacency(
                id=f"ADJ-{i + 1:02d}",
                section_a_id=sections[i].id,
                section_b_id=sections[i + 1].id,
            )
        )

    # Inner sections eligible for a bypass loop (must have predecessor and successor)
    eligible_indices = list(range(1, backbone_count - 1))
    target_loop_count = min(len(eligible_indices), target_loop_count)

    # Sample without replacement; prioritize non-adjacent inner sections for realism
    chosen_indices: list[int] = []
    shuffled_eligible = list(eligible_indices)
    rng.shuffle(shuffled_eligible)
    for idx in shuffled_eligible:
        # Avoid strictly adjacent passing loops sharing both boundaries
        if not any(abs(idx - c) <= 1 for c in chosen_indices):
            chosen_indices.append(idx)
            if len(chosen_indices) >= target_loop_count:
                break

    # If non-adjacent constraint was too strict, backfill up to target_loop_count
    if len(chosen_indices) < target_loop_count:
        for idx in shuffled_eligible:
            if idx not in chosen_indices:
                chosen_indices.append(idx)
                if len(chosen_indices) >= target_loop_count:
                    break

    chosen_indices.sort()

    # Create passing loop sections and attach them to predecessor and successor
    for loop_idx, main_idx in enumerate(chosen_indices):
        main_sec = sections[main_idx]
        pred_sec = sections[main_idx - 1]
        succ_sec = sections[main_idx + 1]

        loop_length = round(main_sec.length_meters * rng.uniform(0.95, 1.15), 1)
        loop_sec_id = f"SEC-LOOP-{main_idx + 1:02d}"
        loop_traffic = "freight" if rng.random() < 0.5 else "passenger"

        loop_sec = TrackSection(
            id=loop_sec_id,
            name=f"Passing Loop Siding {main_idx + 1}",
            length_meters=loop_length,
            attributes={
                "topology": "linear_with_passing_loops",
                "track_type": "siding",
                "is_passing_loop": True,
                "bypasses": main_sec.id,
                "traffic_type": loop_traffic,
            },
        )
        sections.append(loop_sec)

        # Mark mainline section as having a passing loop
        main_sec.attributes["has_passing_loop"] = True
        main_sec.attributes["passing_loop_id"] = loop_sec_id

        # Connect loop section to predecessor and successor
        adjacencies.append(
            SectionAdjacency(
                id=f"ADJ-LOOP-{main_idx + 1:02d}-A",
                section_a_id=pred_sec.id,
                section_b_id=loop_sec.id,
            )
        )
        adjacencies.append(
            SectionAdjacency(
                id=f"ADJ-LOOP-{main_idx + 1:02d}-B",
                section_a_id=loop_sec.id,
                section_b_id=succ_sec.id,
            )
        )

    return sections, adjacencies
