"""Unit tests for the CP-SAT scheduling core in backend/cadence/solve."""

from datetime import datetime, timedelta, timezone
import pytest

from cadence.domain.schemas import (
    MaintenanceTaskSchema,
    TrackSectionSchema,
    TrainSlotSchema,
)
from cadence.generator import generate_synthetic_network
from cadence.profiles.metro import MetroProfile
from cadence.profiles.registry import ProfileRegistry
from cadence.solve import SolveResult, build_cp_model, solve_schedule

BASE_TEST_TIME = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


def test_small_scenario_solves_optimal() -> None:
    """A small synthetic scenario with 4 sections and 4 non-conflicting tasks solves to OPTIMAL."""
    profile = MetroProfile()
    sections = [
        TrackSectionSchema(id=f"S{i}", name=f"Section {i}") for i in range(1, 5)
    ]
    tasks = [
        MaintenanceTaskSchema(
            id="T1",
            name="Task 1",
            section_id="S1",
            duration_minutes=60,
            earliest_start=BASE_TEST_TIME,
            latest_end=BASE_TEST_TIME + timedelta(hours=4),
            priority=2,
        ),
        MaintenanceTaskSchema(
            id="T2",
            name="Task 2",
            section_id="S2",
            duration_minutes=90,
            earliest_start=BASE_TEST_TIME + timedelta(hours=1),
            latest_end=BASE_TEST_TIME + timedelta(hours=5),
            priority=3,
        ),
        MaintenanceTaskSchema(
            id="T3",
            name="Task 3",
            section_id="S3",
            duration_minutes=45,
            earliest_start=BASE_TEST_TIME + timedelta(hours=2),
            latest_end=BASE_TEST_TIME + timedelta(hours=6),
            priority=1,
        ),
        MaintenanceTaskSchema(
            id="T4",
            name="Task 4",
            section_id="S4",
            duration_minutes=120,
            earliest_start=BASE_TEST_TIME + timedelta(hours=3),
            latest_end=BASE_TEST_TIME + timedelta(hours=8),
            priority=4,
        ),
    ]

    result: SolveResult = solve_schedule(
        sections=sections,
        tasks=tasks,
        train_slots=[],
        profile=profile,
        time_horizon_minutes=1440,
        base_time=BASE_TEST_TIME,
    )

    assert result.status == "OPTIMAL"
    assert len(result.scheduled_blocks) == 4
    assert result.objective_value is not None

    task_map = {t.id: t for t in tasks}
    for block in result.scheduled_blocks:
        orig = task_map[block.task_id]
        assert block.start_time >= orig.earliest_start
        assert block.end_time <= orig.latest_end
        duration_sec = (block.end_time - block.start_time).total_seconds()
        assert duration_sec == orig.duration_minutes * 60
        assert block.method == "full_resolve"


def test_two_tasks_same_section_no_overlap() -> None:
    """Two tasks forced onto the same section with overlapping windows produce non-overlapping blocks."""
    profile = MetroProfile()
    section = TrackSectionSchema(id="S1", name="Track Section 1")

    # Both tasks request 60 minutes on section S1 within a 180-minute window
    t1 = MaintenanceTaskSchema(
        id="T1",
        name="Task 1",
        section_id="S1",
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=180),
        priority=1,
    )
    t2 = MaintenanceTaskSchema(
        id="T2",
        name="Task 2",
        section_id="S1",
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=180),
        priority=1,
    )

    result = solve_schedule(
        sections=[section],
        tasks=[t1, t2],
        train_slots=[],
        profile=profile,
        time_horizon_minutes=1440,
        base_time=BASE_TEST_TIME,
    )

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert len(result.scheduled_blocks) == 2

    b1, b2 = result.scheduled_blocks[0], result.scheduled_blocks[1]
    # Verify non-overlapping time intervals
    assert b1.end_time <= b2.start_time or b2.end_time <= b1.start_time, (
        f"Scheduled blocks overlap: [{b1.start_time} - {b1.end_time}] vs [{b2.start_time} - {b2.end_time}]"
    )


def test_intentionally_infeasible_scenario() -> None:
    """Two tasks on the same section with windows too narrow to both fit return INFEASIBLE without crashing."""
    profile = MetroProfile()
    section = TrackSectionSchema(id="S1", name="Track Section 1")

    # Each task requires 60 mins on S1, but total window is only 80 mins (needs at least 120 mins)
    t1 = MaintenanceTaskSchema(
        id="T1",
        name="Task 1",
        section_id="S1",
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=80),
        priority=2,
    )
    t2 = MaintenanceTaskSchema(
        id="T2",
        name="Task 2",
        section_id="S1",
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=80),
        priority=2,
    )

    result = solve_schedule(
        sections=[section],
        tasks=[t1, t2],
        train_slots=[],
        profile=profile,
        time_horizon_minutes=1440,
        base_time=BASE_TEST_TIME,
    )

    assert result.status == "INFEASIBLE"
    assert len(result.scheduled_blocks) == 0


def test_priority_weight_schedules_higher_priority_earlier() -> None:
    """Higher-priority tasks get scheduled earlier within their window than lower-priority tasks."""
    profile = MetroProfile()
    section = TrackSectionSchema(id="S1", name="Shared Track Section")

    # Two 60-minute tasks on the same section with identical windows [0, 180]
    t_high = MaintenanceTaskSchema(
        id="T_HIGH",
        name="High Priority Task",
        section_id="S1",
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=180),
        priority=5,
    )
    t_low = MaintenanceTaskSchema(
        id="T_LOW",
        name="Low Priority Task",
        section_id="S1",
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=180),
        priority=1,
    )

    assert profile.priority_weight(t_high) > profile.priority_weight(t_low)

    result = solve_schedule(
        sections=[section],
        tasks=[t_high, t_low],
        train_slots=[],
        profile=profile,
        time_horizon_minutes=1440,
        base_time=BASE_TEST_TIME,
    )

    assert result.status == "OPTIMAL"
    assert len(result.scheduled_blocks) == 2

    block_map = {b.task_id: b for b in result.scheduled_blocks}
    high_block = block_map["T_HIGH"]
    low_block = block_map["T_LOW"]

    # Higher-priority task should start at the beginning of the window
    assert high_block.start_time < low_block.start_time
    assert high_block.start_time == BASE_TEST_TIME


@pytest.mark.parametrize("profile_name", ["metro", "local", "mainline"])
def test_solve_schedule_all_profiles(profile_name: str) -> None:
    """solve_schedule works correctly when called with each profile on synthetic networks."""
    profile = ProfileRegistry.get(profile_name)
    net = generate_synthetic_network(
        profile_name=profile_name,
        seed=42,
        section_count=8,
        train_count=6,
        task_count=5,
    )

    result = solve_schedule(
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
        time_horizon_minutes=1440,
    )

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert len(result.scheduled_blocks) == len(net["maintenance_tasks"])
    assert result.objective_value is not None


def test_wall_time_populated_and_reasonable() -> None:
    """wall_time_seconds is populated, strictly positive, and does not exceed time_limit_seconds."""
    profile = MetroProfile()
    section = TrackSectionSchema(id="S1", name="Section 1")
    task = MaintenanceTaskSchema(
        id="T1",
        name="Task 1",
        section_id="S1",
        duration_minutes=30,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(hours=2),
    )

    time_limit = 10
    result = solve_schedule(
        sections=[section],
        tasks=[task],
        train_slots=[],
        profile=profile,
        time_limit_seconds=time_limit,
    )

    assert result.wall_time_seconds > 0.0
    assert result.wall_time_seconds <= float(time_limit)
