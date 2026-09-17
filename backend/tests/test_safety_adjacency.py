"""Comprehensive pytest suite for safety-adjacency hard constraints in Cadence CP-SAT solver."""

from datetime import datetime, timedelta, timezone
from typing import Any, Union
import pytest

from cadence.domain.graph import NetworkGraph
from cadence.domain.models import MaintenanceTask, SectionAdjacency, TrackSection, TrainSlot
from cadence.domain.schemas import (
    MaintenanceTaskSchema,
    ScheduledBlockSchema,
    SectionAdjacencySchema,
    TrackSectionSchema,
    TrainSlotSchema,
)
from cadence.generator import generate_synthetic_network
from cadence.profiles.local import LocalProfile
from cadence.profiles.mainline import MainlineProfile
from cadence.profiles.metro import MetroProfile
from cadence.solve import SolveResult, build_cp_model, solve_schedule
from cadence.solve.safety import precompute_unsafe_adjacency_pairs

BASE_TEST_TIME = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


def count_safety_violations(
    scheduled_blocks: list[Union[ScheduledBlockSchema, Any]],
    unsafe_pairs: list[tuple[str, str, ...]],
) -> int:
    """Scan a solved schedule and mechanically count any actual overlap between blocks on flagged-unsafe section pairs.

    This independent ground-truth checker verifies schedule validity by checking all pairs
    of scheduled blocks for overlapping time intervals on sections known to be unsafe together.

    Args:
        scheduled_blocks: List of ScheduledBlockSchema or block objects with section_id,
            start_time, end_time.
        unsafe_pairs: List of tuples (section_a_id, section_b_id, ...) flagged unsafe.

    Returns:
        int: Number of pairwise temporal overlaps on unsafe adjacent section pairs.
    """
    unsafe_set: set[tuple[str, str]] = set()
    for pair in unsafe_pairs:
        u = pair[0]
        v = pair[1]
        unsafe_set.add(tuple(sorted((u, v))))

    violations = 0
    n = len(scheduled_blocks)
    for i in range(n):
        b1 = scheduled_blocks[i]
        sec1 = b1.section_id if hasattr(b1, "section_id") else b1["section_id"]
        start1 = b1.start_time if hasattr(b1, "start_time") else b1["start_time"]
        end1 = b1.end_time if hasattr(b1, "end_time") else b1["end_time"]

        for j in range(i + 1, n):
            b2 = scheduled_blocks[j]
            sec2 = b2.section_id if hasattr(b2, "section_id") else b2["section_id"]

            pair_key = tuple(sorted((sec1, sec2)))
            if pair_key in unsafe_set:
                start2 = b2.start_time if hasattr(b2, "start_time") else b2["start_time"]
                end2 = b2.end_time if hasattr(b2, "end_time") else b2["end_time"]

                # Overlap exists iff max(start1, start2) < min(end1, end2)
                if max(start1, start2) < min(end1, end2):
                    violations += 1

    return violations


# =============================================================================
# 1. MetroProfile on small loop networks (>= 20 random seeds)
# =============================================================================


@pytest.mark.parametrize("seed", list(range(25)))
def test_metro_small_loop_safety_constraint_across_seeds(seed: int) -> None:
    """On a small loop network across 25 seeds, tasks on unsafe adjacent sections NEVER overlap."""
    profile = MetroProfile()
    net = generate_synthetic_network(
        profile_name="metro",
        seed=seed,
        section_count=3,
        train_count=2,
        task_count=0,
    )
    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])
    unsafe_pairs = precompute_unsafe_adjacency_pairs(graph, profile, net["sections"])

    assert len(unsafe_pairs) > 0, f"Expected unsafe pairs on 3-section loop at seed {seed}"

    # Pick an unsafe pair
    sec_a, sec_b, reason = unsafe_pairs[0]

    # Two 60-minute tasks on the unsafe adjacent sections with overlapping [0, 180] windows
    # A naive solver ignoring safety-adjacency would schedule both at [0, 60]
    task_a = MaintenanceTaskSchema(
        id="TASK_A",
        name="Task A on Section A",
        section_id=sec_a,
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=180),
        priority=3,
    )
    task_b = MaintenanceTaskSchema(
        id="TASK_B",
        name="Task B on Section B",
        section_id=sec_b,
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=180),
        priority=3,
    )

    result = solve_schedule(
        sections=net["sections"],
        tasks=[task_a, task_b],
        train_slots=[],
        profile=profile,
        time_horizon_minutes=240,
        base_time=BASE_TEST_TIME,
        graph=graph,
        adjacencies=net["adjacencies"],
    )

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert len(result.scheduled_blocks) == 2

    # Ground-truth verification: exactly zero safety violations
    violations = count_safety_violations(result.scheduled_blocks, unsafe_pairs)
    assert violations == 0, f"Seed {seed} produced {violations} safety-adjacency violations"

    # Explicit check: blocks do not overlap in time
    block_map = {b.task_id: b for b in result.scheduled_blocks}
    ba = block_map["TASK_A"]
    bb = block_map["TASK_B"]
    assert ba.end_time <= bb.start_time or bb.end_time <= ba.start_time, (
        f"Blocks overlap on unsafe sections {sec_a} & {sec_b}: [{ba.start_time} - {ba.end_time}] vs [{bb.start_time} - {bb.end_time}]"
    )


# =============================================================================
# 2. MainlineProfile: stranded passing loop scenario (>= 20 random seeds)
# =============================================================================


@pytest.mark.parametrize("seed", list(range(25)))
def test_mainline_passing_loop_safety_constraint_across_seeds(seed: int) -> None:
    """On mainline passing loops across 25 seeds, blocking both sections of an unsafe pair is forbidden."""
    profile = MainlineProfile()
    net = generate_synthetic_network(
        profile_name="mainline",
        seed=seed,
        section_count=10,
        train_count=4,
        task_count=0,
    )
    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])
    unsafe_pairs = precompute_unsafe_adjacency_pairs(graph, profile, net["sections"])

    assert len(unsafe_pairs) > 0, f"Expected unsafe pairs on mainline network at seed {seed}"

    # Pick an unsafe pair that strands passing loops
    sec_a, sec_b, reason = unsafe_pairs[0]

    task_1 = MaintenanceTaskSchema(
        id="TASK_MAIN_1",
        name=f"Mainline Work on {sec_a}",
        section_id=sec_a,
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=180),
        priority=2,
    )
    task_2 = MaintenanceTaskSchema(
        id="TASK_MAIN_2",
        name=f"Mainline Work on {sec_b}",
        section_id=sec_b,
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=180),
        priority=2,
    )

    result = solve_schedule(
        sections=net["sections"],
        tasks=[task_1, task_2],
        train_slots=[],
        profile=profile,
        time_horizon_minutes=240,
        base_time=BASE_TEST_TIME,
        graph=graph,
        adjacencies=net["adjacencies"],
    )

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert len(result.scheduled_blocks) == 2

    violations = count_safety_violations(result.scheduled_blocks, unsafe_pairs)
    assert violations == 0, f"Seed {seed} produced {violations} safety violations on mainline passing loop"

    block_map = {b.task_id: b for b in result.scheduled_blocks}
    b1 = block_map["TASK_MAIN_1"]
    b2 = block_map["TASK_MAIN_2"]
    assert b1.end_time <= b2.start_time or b2.end_time <= b1.start_time


# =============================================================================
# 3. LocalProfile: isolated mixed-station scenario (>= 20 random seeds)
# =============================================================================


@pytest.mark.parametrize("seed", list(range(25)))
def test_local_isolated_mixed_station_safety_constraint_across_seeds(seed: int) -> None:
    """On local networks across 25 seeds, blocking sections that isolate a mixed express/local stop is forbidden."""
    profile = LocalProfile()
    net = generate_synthetic_network(
        profile_name="local",
        seed=seed,
        section_count=4,
        train_count=6,
        task_count=0,
    )
    # Ensure at least one section is tagged as mixed express/local if not already present
    sections = net["sections"]
    if not any(s.attributes.get("has_express_and_local") for s in sections):
        sections[-1].attributes["has_express_and_local"] = True
        sections[-1].attributes["train_types"] = ["express", "local"]

    graph = NetworkGraph.build_from_sections(sections, net["adjacencies"])
    unsafe_pairs = precompute_unsafe_adjacency_pairs(
        graph=graph,
        profile=profile,
        sections=sections,
        train_slots=net["train_slots"],
    )

    assert len(unsafe_pairs) > 0, f"Expected unsafe pairs for LocalProfile at seed {seed}"

    sec_a, sec_b, reason = unsafe_pairs[0]

    task_loc_1 = MaintenanceTaskSchema(
        id="TASK_LOC_1",
        name=f"Local Task 1 on {sec_a}",
        section_id=sec_a,
        duration_minutes=45,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=150),
        priority=2,
    )
    task_loc_2 = MaintenanceTaskSchema(
        id="TASK_LOC_2",
        name=f"Local Task 2 on {sec_b}",
        section_id=sec_b,
        duration_minutes=45,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=150),
        priority=2,
    )

    result = solve_schedule(
        sections=sections,
        tasks=[task_loc_1, task_loc_2],
        train_slots=net["train_slots"],
        profile=profile,
        time_horizon_minutes=240,
        base_time=BASE_TEST_TIME,
        graph=graph,
        adjacencies=net["adjacencies"],
    )

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert len(result.scheduled_blocks) == 2

    violations = count_safety_violations(result.scheduled_blocks, unsafe_pairs)
    assert violations == 0, f"Seed {seed} produced {violations} safety violations on local profile"

    block_map = {b.task_id: b for b in result.scheduled_blocks}
    b1 = block_map["TASK_LOC_1"]
    b2 = block_map["TASK_LOC_2"]
    assert b1.end_time <= b2.start_time or b2.end_time <= b1.start_time


# =============================================================================
# 4. Monotonicity under safety: raising priority never compromises safety
# =============================================================================


@pytest.mark.parametrize("seed", [0, 5, 12, 42, 77, 99])
def test_monotonicity_under_safety_priority_escalation(seed: int) -> None:
    """Raising a task's priority or emergency status NEVER causes the solver to violate safety-adjacency."""
    profile = MetroProfile()
    net = generate_synthetic_network(
        profile_name="metro",
        seed=seed,
        section_count=3,
        train_count=0,
        task_count=0,
    )
    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])
    unsafe_pairs = precompute_unsafe_adjacency_pairs(graph, profile, net["sections"])
    sec_a, sec_b, _ = unsafe_pairs[0]

    # Priority escalation levels:
    # 1. Low vs Low
    # 2. Maximum priority (5 vs 5)
    # 3. Emergency vs Routine
    # 4. Both emergency (huge pressure to start at minute 0)
    priority_scenarios = [
        {"p_a": 1, "em_a": False, "p_b": 1, "em_b": False},
        {"p_a": 5, "em_a": False, "p_b": 5, "em_b": False},
        {"p_a": 5, "em_a": True, "p_b": 1, "em_b": False},
        {"p_a": 5, "em_a": True, "p_b": 5, "em_b": True},
    ]

    for sc in priority_scenarios:
        t_a = MaintenanceTaskSchema(
            id="TASK_A",
            name="Task A",
            section_id=sec_a,
            duration_minutes=60,
            earliest_start=BASE_TEST_TIME,
            latest_end=BASE_TEST_TIME + timedelta(minutes=180),
            priority=sc["p_a"],
            is_emergency=sc["em_a"],
        )
        t_b = MaintenanceTaskSchema(
            id="TASK_B",
            name="Task B",
            section_id=sec_b,
            duration_minutes=60,
            earliest_start=BASE_TEST_TIME,
            latest_end=BASE_TEST_TIME + timedelta(minutes=180),
            priority=sc["p_b"],
            is_emergency=sc["em_b"],
        )

        res = solve_schedule(
            sections=net["sections"],
            tasks=[t_a, t_b],
            train_slots=[],
            profile=profile,
            time_horizon_minutes=240,
            base_time=BASE_TEST_TIME,
            graph=graph,
            adjacencies=net["adjacencies"],
        )

        assert res.status in ("OPTIMAL", "FEASIBLE")
        violations = count_safety_violations(res.scheduled_blocks, unsafe_pairs)
        assert violations == 0, (
            f"Seed {seed}, scenario {sc} violated safety constraint! Violations={violations}"
        )

        block_map = {b.task_id: b for b in res.scheduled_blocks}
        ba = block_map["TASK_A"]
        bb = block_map["TASK_B"]
        assert ba.end_time <= bb.start_time or bb.end_time <= ba.start_time


# =============================================================================
# 5. Explicit Adversarial Test: Train Disruption Penalty vs. Safety Hardness
# =============================================================================


def test_adversarial_train_disruption_vs_safety() -> None:
    """Deliberately construct a scenario where satisfying train slot disruption penalties optimally
    would require violating safety-adjacency. Confirm the solver respects safety over objective optimality.
    """
    profile = MetroProfile()

    # Small 3-node loop network: S1 - S2 - S3 - S1
    sections = [
        TrackSectionSchema(id="S1", name="Section 1"),
        TrackSectionSchema(id="S2", name="Section 2"),
        TrackSectionSchema(id="S3", name="Section 3"),
    ]
    adjacencies = [
        SectionAdjacencySchema(section_a_id="S1", section_b_id="S2"),
        SectionAdjacencySchema(section_a_id="S2", section_b_id="S3"),
        SectionAdjacencySchema(section_a_id="S3", section_b_id="S1"),
    ]
    graph = NetworkGraph.build_from_sections(sections, adjacencies)
    unsafe_pairs = precompute_unsafe_adjacency_pairs(graph, profile, sections)
    assert any(tuple(sorted((p[0], p[1]))) == ("S1", "S2") for p in unsafe_pairs)

    # Setup:
    # Task 1 on S1: 60 minutes, window [0, 120]
    # Task 2 on S2: 60 minutes, window [0, 120]
    #
    # Trains:
    # Train 1 runs on S1 during [60, 120] with massive priority (priority=10).
    # Train 2 runs on S2 during [60, 120] with massive priority (priority=10).
    #
    # Conflict:
    # At time [0, 60], NEITHER section has a train slot!
    # If safety-adjacency were soft or tunable, an objective-minimizing solver would place
    # Task 1 at [0, 60] and Task 2 at [0, 60], yielding disruption penalty = 0.
    # But S1 and S2 are an unsafe pair!
    # Because safety is a structural HARD constraint, the solver CANNOT place both at [0, 60].
    # One task MUST be pushed to [60, 120], directly disrupting a train slot and incurring
    # a high disruption penalty.
    task_1 = MaintenanceTaskSchema(
        id="TASK_S1",
        name="Maintenance S1",
        section_id="S1",
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=120),
        priority=1,
    )
    task_2 = MaintenanceTaskSchema(
        id="TASK_S2",
        name="Maintenance S2",
        section_id="S2",
        duration_minutes=60,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(minutes=120),
        priority=1,
    )

    train_1 = TrainSlotSchema(
        id="TRAIN_1",
        name="High Priority Train on S1",
        scheduled_start=BASE_TEST_TIME + timedelta(minutes=60),
        scheduled_end=BASE_TEST_TIME + timedelta(minutes=120),
        route=["S1"],
        priority=10,
    )
    train_2 = TrainSlotSchema(
        id="TRAIN_2",
        name="High Priority Train on S2",
        scheduled_start=BASE_TEST_TIME + timedelta(minutes=60),
        scheduled_end=BASE_TEST_TIME + timedelta(minutes=120),
        route=["S2"],
        priority=10,
    )

    result = solve_schedule(
        sections=sections,
        tasks=[task_1, task_2],
        train_slots=[train_1, train_2],
        profile=profile,
        time_horizon_minutes=180,
        base_time=BASE_TEST_TIME,
        graph=graph,
        adjacencies=adjacencies,
    )

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert len(result.scheduled_blocks) == 2

    # Verify zero safety violations
    violations = count_safety_violations(result.scheduled_blocks, unsafe_pairs)
    assert violations == 0, "Adversarial test produced safety violations!"

    # Verify that blocks on S1 and S2 strictly do not overlap
    b1, b2 = result.scheduled_blocks[0], result.scheduled_blocks[1]
    assert b1.end_time <= b2.start_time or b2.end_time <= b1.start_time

    # Objective value MUST be strictly positive because the solver was forced to incur
    # disruption / delay rather than violating safety-adjacency
    assert result.objective_value is not None
    assert result.objective_value > 0.0, (
        f"Expected positive objective penalty due to train disruption, got {result.objective_value}"
    )


# =============================================================================
# 6. Precomputation and Model Structure Verification
# =============================================================================


def test_precompute_unsafe_adjacency_pairs_structure_and_reasons() -> None:
    """precompute_unsafe_adjacency_pairs preserves reasons and checks adjacent pairs via get_neighbors."""
    profile = MainlineProfile()
    net = generate_synthetic_network("mainline", seed=42, section_count=8)
    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])

    unsafe = precompute_unsafe_adjacency_pairs(graph, profile, net["sections"])
    assert len(unsafe) > 0

    for item in unsafe:
        assert len(item) == 3
        sec_a, sec_b, reason = item
        assert isinstance(sec_a, str) and len(sec_a) > 0
        assert isinstance(sec_b, str) and len(sec_b) > 0
        assert isinstance(reason, str) and len(reason) > 0
        assert "unsafe" in reason.lower()
        # Verify that sec_a and sec_b are indeed neighbors in the graph
        assert graph.are_adjacent(sec_a, sec_b)


def test_build_cp_model_returns_dict_with_unsafe_pairs_and_allows_unpacking() -> None:
    """build_cp_model returns a dictionary with 'unsafe_adjacency_pairs' and preserves 2-tuple unpacking."""
    profile = MetroProfile()
    sections = [
        TrackSectionSchema(id="S1", name="Section 1"),
        TrackSectionSchema(id="S2", name="Section 2"),
        TrackSectionSchema(id="S3", name="Section 3"),
    ]
    adjacencies = [
        SectionAdjacencySchema(section_a_id="S1", section_b_id="S2"),
        SectionAdjacencySchema(section_a_id="S2", section_b_id="S3"),
        SectionAdjacencySchema(section_a_id="S3", section_b_id="S1"),
    ]
    graph = NetworkGraph.build_from_sections(sections, adjacencies)

    task = MaintenanceTaskSchema(
        id="T1",
        name="Task 1",
        section_id="S1",
        duration_minutes=30,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(hours=2),
    )

    # 1. Dict-style access
    res = build_cp_model(
        sections=sections,
        tasks=[task],
        train_slots=[],
        profile=profile,
        time_horizon_minutes=180,
        base_time=BASE_TEST_TIME,
        graph=graph,
    )
    assert isinstance(res, dict)
    assert "model" in res
    assert "interval_vars" in res
    assert "unsafe_adjacency_pairs" in res
    assert len(res["unsafe_adjacency_pairs"]) > 0

    # 2. Tuple unpacking backwards compatibility
    model, interval_vars = build_cp_model(
        sections=sections,
        tasks=[task],
        train_slots=[],
        profile=profile,
        time_horizon_minutes=180,
        base_time=BASE_TEST_TIME,
        graph=graph,
    )
    assert model is not None
    assert "T1" in interval_vars


def test_solve_result_includes_unsafe_adjacency_pairs() -> None:
    """SolveResult exposes unsafe_adjacency_pairs downstream for inspection and debugging."""
    profile = MetroProfile()
    sections = [
        TrackSectionSchema(id="S1", name="Section 1"),
        TrackSectionSchema(id="S2", name="Section 2"),
        TrackSectionSchema(id="S3", name="Section 3"),
    ]
    adjacencies = [
        SectionAdjacencySchema(section_a_id="S1", section_b_id="S2"),
        SectionAdjacencySchema(section_a_id="S2", section_b_id="S3"),
        SectionAdjacencySchema(section_a_id="S3", section_b_id="S1"),
    ]
    task = MaintenanceTaskSchema(
        id="T1",
        name="Task 1",
        section_id="S1",
        duration_minutes=30,
        earliest_start=BASE_TEST_TIME,
        latest_end=BASE_TEST_TIME + timedelta(hours=2),
    )

    result = solve_schedule(
        sections=sections,
        tasks=[task],
        train_slots=[],
        profile=profile,
        adjacencies=adjacencies,
    )

    assert hasattr(result, "unsafe_adjacency_pairs")
    assert isinstance(result.unsafe_adjacency_pairs, list)
    assert len(result.unsafe_adjacency_pairs) > 0
    assert any("S1" in p and "S2" in p for p in result.unsafe_adjacency_pairs)
