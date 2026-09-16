"""Main entry point for Cadence synthetic network generation and database persistence."""

import random
from typing import Any, Optional

from sqlalchemy.orm import Session

from cadence.generator.tasks import generate_maintenance_tasks
from cadence.generator.topology import (
    generate_linear_topology,
    generate_linear_with_passing_loops_topology,
    generate_loop_topology,
)
from cadence.generator.trains import generate_train_slots
from cadence.profiles.registry import ProfileRegistry

DEFAULT_DEMO_MAX_SECTIONS = 25


def generate_synthetic_network(
    profile_name: str,
    seed: int,
    section_count: Optional[int] = None,
    train_count: int = 20,
    task_count: int = 15,
    time_window_hours: int = 24,
) -> dict[str, Any]:
    """Generate a reproducible synthetic railway network dataset shaped by a NetworkProfile.

    Args:
        profile_name: Name of registered NetworkProfile (e.g. 'metro', 'mainline', 'local').
        seed: Integer seed for deterministic random generation.
        section_count: Optional custom track section count. If None, derives from profile hint
            capped at ~25 sections.
        train_count: Number of train slots to generate.
        task_count: Number of maintenance tasks to generate.
        time_window_hours: Planning horizon in hours.

    Returns:
        dict[str, Any]: Generated dataset with keys 'sections', 'adjacencies', 'train_slots',
            'maintenance_tasks', 'profile_name', 'seed'.
    """
    profile = ProfileRegistry.get(profile_name)
    hint = profile.default_topology_generator_hint()
    topo_type = hint.get("topology_type", profile.default_topology)

    # Pick sensible default section_count if not explicitly provided
    if section_count is None:
        avg_hint = hint.get("avg_section_count", 15)
        effective_section_count = min(DEFAULT_DEMO_MAX_SECTIONS, int(avg_hint))
    else:
        effective_section_count = section_count

    # Single seeded RNG shared across generators in strict sequential order for reproducibility
    rng = random.Random(seed)

    # 1. Topology generation
    if topo_type == "loop":
        sections, adjacencies = generate_loop_topology(
            section_count=effective_section_count,
            rng=rng,
        )
    elif topo_type == "linear_with_passing_loops":
        loop_freq = hint.get("loop_frequency", 0.2)
        sections, adjacencies = generate_linear_with_passing_loops_topology(
            section_count=effective_section_count,
            rng=rng,
            loop_frequency=loop_freq,
        )
    elif topo_type == "linear":
        has_express = hint.get("has_express_stops", True)
        sections, adjacencies = generate_linear_topology(
            section_count=effective_section_count,
            rng=rng,
            has_express_stops=has_express,
        )
    else:
        sections, adjacencies = generate_linear_topology(
            section_count=effective_section_count,
            rng=rng,
        )

    # 2. Train slots generation
    train_slots = generate_train_slots(
        sections=sections,
        profile=profile,
        rng=rng,
        count=train_count,
        time_window_hours=time_window_hours,
        adjacencies=adjacencies,
    )

    # 3. Maintenance tasks generation
    maintenance_tasks = generate_maintenance_tasks(
        sections=sections,
        profile=profile,
        rng=rng,
        count=task_count,
        time_window_hours=time_window_hours,
    )

    return {
        "sections": sections,
        "adjacencies": adjacencies,
        "train_slots": train_slots,
        "maintenance_tasks": maintenance_tasks,
        "profile_name": profile.profile_name,
        "seed": seed,
    }


def persist_to_db(generated: dict[str, Any], session: Session) -> None:
    """Write generated synthetic network entities to the database using SQLAlchemy models.

    Args:
        generated: Synthetic network dict returned by generate_synthetic_network.
        session: Active SQLAlchemy Session.
    """
    sections = generated.get("sections", [])
    adjacencies = generated.get("adjacencies", [])
    train_slots = generated.get("train_slots", [])
    maintenance_tasks = generated.get("maintenance_tasks", [])

    # Add TrackSections first (parents for foreign keys)
    for section in sections:
        session.add(section)
    session.flush()

    # Add SectionAdjacencies and TrainSlots
    for adj in adjacencies:
        session.add(adj)
    for slot in train_slots:
        session.add(slot)
    session.flush()

    # Add MaintenanceTasks (depend on track_sections.id)
    for task in maintenance_tasks:
        session.add(task)
    session.flush()

    session.commit()
