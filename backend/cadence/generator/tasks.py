"""Maintenance task generation shaped by network topology and profile attributes."""

from datetime import datetime, timedelta, timezone
import random
from typing import Any, Optional

from cadence.domain.models import MaintenanceTask, TrackSection
from cadence.profiles.base import NetworkProfile

DEFAULT_BASE_TIME = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


def generate_maintenance_tasks(
    sections: list[TrackSection],
    profile: NetworkProfile,
    rng: random.Random,
    count: int,
    time_window_hours: int = 24,
    emergency_fraction: float = 0.1,
    passenger_fraction: float = 0.6,
    base_time: Optional[datetime] = None,
) -> list[MaintenanceTask]:
    """Generate synthetic maintenance tasks across sections with profile-appropriate tagging.

    Args:
        sections: Track sections available in the network.
        profile: Active NetworkProfile.
        rng: Seeded random number generator instance.
        count: Number of maintenance tasks to generate.
        time_window_hours: Planning horizon in hours.
        emergency_fraction: Approximate fraction of tasks tagged as emergency.
        passenger_fraction: Configurable fraction of passenger tasks for MainlineProfile.
        base_time: Deterministic reference datetime for time horizons.

    Returns:
        list[MaintenanceTask]: Generated maintenance tasks.
    """
    if not sections or count <= 0:
        return []

    ref_time = base_time if base_time is not None else DEFAULT_BASE_TIME
    total_minutes = time_window_hours * 60
    tasks: list[MaintenanceTask] = []

    for i in range(count):
        # Pick random target section
        sec = rng.choice(sections)

        # Determine emergency status
        is_emergency = rng.random() < emergency_fraction

        # Duration & Priority
        if is_emergency:
            duration_minutes = rng.choice([30, 45, 60, 90, 120])
            priority = rng.randint(4, 5)
        else:
            duration_minutes = rng.choice([60, 90, 120, 180, 240, 300])
            priority = rng.randint(1, 3)

        # Attribute tagging by profile
        name, attrs = _build_task_attributes(
            profile=profile,
            section=sec,
            index=i,
            is_emergency=is_emergency,
            rng=rng,
            passenger_fraction=passenger_fraction,
        )

        # Window generation within time horizon
        max_start = max(0, total_minutes - duration_minutes - 30)
        start_minute = rng.randint(0, max_start)
        earliest_start = ref_time + timedelta(minutes=start_minute)

        if is_emergency:
            # Tighter execution window for emergency tasks
            slack = rng.randint(15, 60)
        else:
            # Wider window for routine tasks
            slack = rng.randint(120, 420)

        end_minute = min(total_minutes, start_minute + duration_minutes + slack)
        latest_end = ref_time + timedelta(minutes=end_minute)

        # Ensure window is at least duration_minutes long
        min_latest_end = earliest_start + timedelta(minutes=duration_minutes)
        if latest_end < min_latest_end:
            latest_end = min_latest_end

        task = MaintenanceTask(
            id=f"TASK-{i + 1:03d}",
            name=name,
            section_id=sec.id,
            duration_minutes=duration_minutes,
            earliest_start=earliest_start,
            latest_end=latest_end,
            priority=priority,
            is_emergency=is_emergency,
            attributes=attrs,
        )
        tasks.append(task)

    return tasks


def _build_task_attributes(
    profile: NetworkProfile,
    section: TrackSection,
    index: int,
    is_emergency: bool,
    rng: random.Random,
    passenger_fraction: float,
) -> tuple[str, dict[str, Any]]:
    """Build domain-rich names and attributes matching profile requirements."""
    p_name = profile.profile_name.lower()
    prefix = "Emergency " if is_emergency else "Routine "

    if p_name == "mainline":
        # Tag freight vs passenger matching MainlineProfile priority logic
        sec_traffic = section.attributes.get("traffic_type")
        if sec_traffic in ("passenger", "freight"):
            is_passenger = sec_traffic == "passenger"
        else:
            is_passenger = rng.random() < passenger_fraction

        traffic_type = "passenger" if is_passenger else "freight"
        name = f"Mainline {prefix}{traffic_type.capitalize()} Maintenance {index + 1:02d}"
        attrs: dict[str, Any] = {
            "traffic_type": traffic_type,
            "is_passenger": is_passenger,
            "service_type": traffic_type,
        }
    elif p_name == "local":
        affects_express = section.attributes.get("has_express_and_local", False) or (rng.random() < 0.4)
        name = f"Local {prefix}Track Possession {index + 1:02d}"
        attrs = {
            "affects_express": affects_express,
            "conflict_type": "express" if affects_express else "local",
            "train_types": ["express", "local"] if affects_express else ["local"],
        }
    elif p_name == "metro":
        name = f"Metro {prefix}Track Work {index + 1:02d}"
        attrs = {
            "work_type": "emergency_repair" if is_emergency else "night_possession",
        }
    else:
        name = f"{prefix}Maintenance Task {index + 1:02d}"
        attrs = {}

    return name, attrs
