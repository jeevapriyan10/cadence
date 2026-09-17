"""Comprehensive unit and integration tests for the Reason post-hoc explainability layer."""

from datetime import datetime, timedelta, timezone
import pytest

from cadence.domain.graph import NetworkGraph
from cadence.domain.schemas import (
    MaintenanceTaskSchema,
    SectionAdjacencySchema,
    TrackSectionSchema,
    TrainSlotSchema,
)
from cadence.generator import generate_synthetic_network
from cadence.profiles.metro import MetroProfile
from cadence.profiles.registry import ProfileRegistry
from cadence.reason import (
    Explanation,
    RejectedCandidate,
    check_candidate_feasibility,
    explain_full_schedule,
    explain_task_scheduling,
    explanation_to_dict,
    format_explanation,
    generate_candidate_slots,
)
from cadence.solve import build_cp_model, solve_schedule
from cadence.solve.safety import precompute_unsafe_adjacency_pairs

BASE_TIME = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


def test_explain_same_section_task_conflict() -> None:
    """A task delayed behind a higher-priority task on the same section identifies rejected candidates with conflicting_entity_type='task'."""
    profile = MetroProfile()
    section = TrackSectionSchema(id="SEC_01", name="Main Track Section 01")

    # Higher priority task T_PRIORITY takes [06:00, 07:00]
    t_priority = MaintenanceTaskSchema(
        id="T_PRIORITY",
        name="Urgent Track Work",
        section_id="SEC_01",
        duration_minutes=60,
        earliest_start=BASE_TIME,
        latest_end=BASE_TIME + timedelta(hours=4),
        priority=5,
    )

    # Lower priority task T_DELAYED competes for the same window [06:00, 10:00]
    t_delayed = MaintenanceTaskSchema(
        id="T_DELAYED",
        name="Routine Inspection",
        section_id="SEC_01",
        duration_minutes=60,
        earliest_start=BASE_TIME,
        latest_end=BASE_TIME + timedelta(hours=4),
        priority=1,
    )

    sections = [section]
    tasks = [t_priority, t_delayed]
    train_slots: list[TrainSlotSchema] = []

    res = solve_schedule(sections, tasks, train_slots, profile, time_horizon_minutes=240, base_time=BASE_TIME)
    ctx = build_cp_model(sections, tasks, train_slots, profile, time_horizon_minutes=240, base_time=BASE_TIME)

    assert res.status in ("OPTIMAL", "FEASIBLE")

    # Explain the later scheduled task
    explanation = explain_task_scheduling("T_DELAYED", res, ctx, sections, tasks, train_slots, profile)

    assert isinstance(explanation, Explanation)
    assert explanation.task_id == "T_DELAYED"
    assert explanation.scheduled_start >= BASE_TIME + timedelta(minutes=60)
    assert "T_PRIORITY" in explanation.chosen_reason

    # Must contain at least one rejected candidate with conflicting_entity_type="task" pointing to T_PRIORITY
    task_conflicts = [c for c in explanation.rejected_candidates if c.conflicting_entity_type == "task"]
    assert len(task_conflicts) > 0

    first_conflict = task_conflicts[0]
    assert first_conflict.conflicting_entity_id == "T_PRIORITY"
    assert "T_PRIORITY" in first_conflict.rejection_reason
    assert "SEC_01" in first_conflict.rejection_reason


def test_explain_safety_adjacency_conflict() -> None:
    """A task on a section adjacent to an active block in an unsafe pair identifies rejected candidates with conflicting_entity_type='safety_adjacency' and matching profile reason."""
    profile = MetroProfile()

    # 3-node loop network where blocking adjacent pairs is flagged unsafe
    sections = [
        TrackSectionSchema(id="S1", name="Loop Section 1"),
        TrackSectionSchema(id="S2", name="Loop Section 2"),
        TrackSectionSchema(id="S3", name="Loop Section 3"),
    ]
    adjacencies = [
        SectionAdjacencySchema(section_a_id="S1", section_b_id="S2"),
        SectionAdjacencySchema(section_a_id="S2", section_b_id="S3"),
        SectionAdjacencySchema(section_a_id="S3", section_b_id="S1"),
    ]
    graph = NetworkGraph.build_from_sections(sections, adjacencies)
    unsafe_pairs = precompute_unsafe_adjacency_pairs(graph, profile, sections)
    assert len(unsafe_pairs) > 0

    # Retrieve known unsafe reason from profile for pair S1 and S2
    expected_safe, expected_reason = profile.is_safe_adjacency(graph, "S1", "S2")
    assert not expected_safe

    # Task 1 on S1 (high priority, scheduled at [06:00, 07:00])
    task_1 = MaintenanceTaskSchema(
        id="TASK_S1",
        name="Emergency Rail Replacement",
        section_id="S1",
        duration_minutes=60,
        earliest_start=BASE_TIME,
        latest_end=BASE_TIME + timedelta(hours=4),
        priority=5,
    )

    # Task 2 on S2 (lower priority, window [06:00, 10:00])
    task_2 = MaintenanceTaskSchema(
        id="TASK_S2",
        name="Routine Signalling Maintenance",
        section_id="S2",
        duration_minutes=60,
        earliest_start=BASE_TIME,
        latest_end=BASE_TIME + timedelta(hours=4),
        priority=1,
    )

    tasks = [task_1, task_2]
    res = solve_schedule(sections, tasks, [], profile, time_horizon_minutes=240, base_time=BASE_TIME, graph=graph, adjacencies=adjacencies)
    ctx = build_cp_model(sections, tasks, [], profile, time_horizon_minutes=240, base_time=BASE_TIME, graph=graph, adjacencies=adjacencies)

    assert res.status in ("OPTIMAL", "FEASIBLE")

    # Explain Task 2, which was pushed later to avoid the unsafe adjacency conflict
    explanation = explain_task_scheduling("TASK_S2", res, ctx, sections, tasks, [], profile)

    assert isinstance(explanation, Explanation)
    assert explanation.task_id == "TASK_S2"

    safety_conflicts = [
        c for c in explanation.rejected_candidates if c.conflicting_entity_type == "safety_adjacency"
    ]
    assert len(safety_conflicts) > 0

    # Verify that the rejection_reason exactly matches the original reason string from profile's is_safe_adjacency
    matching_reason_found = any(c.rejection_reason == expected_reason for c in safety_conflicts)
    assert matching_reason_found, (
        f"Expected rejection reason '{expected_reason}', got {[c.rejection_reason for c in safety_conflicts]}"
    )
    assert any(c.conflicting_entity_id == "S1" for c in safety_conflicts)


def test_explain_task_without_conflicts() -> None:
    """A task with no competing conflicts produces a valid Explanation with sensible chosen_reason and empty/minimal rejected candidates."""
    profile = MetroProfile()
    section = TrackSectionSchema(id="S_SOLO", name="Isolated Spur Section")
    task = MaintenanceTaskSchema(
        id="T_SOLO",
        name="Solo Maintenance",
        section_id="S_SOLO",
        duration_minutes=60,
        earliest_start=BASE_TIME,
        latest_end=BASE_TIME + timedelta(hours=3),
        priority=2,
    )

    sections = [section]
    tasks = [task]
    res = solve_schedule(sections, tasks, [], profile, time_horizon_minutes=180, base_time=BASE_TIME)
    ctx = build_cp_model(sections, tasks, [], profile, time_horizon_minutes=180, base_time=BASE_TIME)

    assert res.status in ("OPTIMAL", "FEASIBLE")

    explanation = explain_task_scheduling("T_SOLO", res, ctx, sections, tasks, [], profile)

    assert isinstance(explanation, Explanation)
    assert explanation.task_id == "T_SOLO"
    assert explanation.scheduled_start == BASE_TIME
    assert "earliest possible start" in explanation.chosen_reason.lower()
    # No conflicts were encountered, so rejected_candidates should be empty
    assert len(explanation.rejected_candidates) == 0


@pytest.mark.parametrize("profile_name", ["metro", "local", "mainline"])
def test_explain_full_schedule_all_profiles(profile_name: str) -> None:
    """explain_full_schedule produces exactly one Explanation per scheduled task across all three profiles on real solver output."""
    profile = ProfileRegistry.get(profile_name)
    net = generate_synthetic_network(
        profile_name=profile_name,
        seed=42,
        section_count=8,
        train_count=6,
        task_count=5,
    )

    res = solve_schedule(
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
        time_horizon_minutes=1440,
        adjacencies=net["adjacencies"],
    )
    ctx = build_cp_model(
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
        time_horizon_minutes=1440,
        adjacencies=net["adjacencies"],
    )

    assert res.status in ("OPTIMAL", "FEASIBLE")

    explanations = explain_full_schedule(
        solve_result=res,
        model_context=ctx,
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
    )

    # Exactly one explanation per scheduled task, none missing, none duplicated
    assert len(explanations) == len(res.scheduled_blocks)
    explained_ids = [e.task_id for e in explanations]
    scheduled_ids = [b.task_id for b in res.scheduled_blocks]
    assert set(explained_ids) == set(scheduled_ids)
    assert len(set(explained_ids)) == len(explained_ids)

    for expl in explanations:
        assert isinstance(expl, Explanation)
        assert expl.task_name
        assert expl.scheduled_start < expl.scheduled_end
        assert len(expl.chosen_reason) > 0


def test_format_explanation_variations() -> None:
    """format_explanation produces readable non-empty text for both tasks with and without rejected candidates."""
    # 1. Without rejected candidates
    solo_expl = Explanation(
        task_id="T1",
        task_name="Bridge Inspection",
        scheduled_start=BASE_TIME,
        scheduled_end=BASE_TIME + timedelta(hours=1),
        chosen_reason="Scheduled at earliest possible start window.",
        rejected_candidates=[],
    )
    formatted_solo = format_explanation(solo_expl)
    assert "Bridge Inspection" in formatted_solo
    assert "T1" in formatted_solo
    assert "No alternative candidate slots were rejected" in formatted_solo

    # 2. With rejected candidates
    cand1 = RejectedCandidate(
        candidate_start=BASE_TIME,
        candidate_end=BASE_TIME + timedelta(hours=1),
        rejection_reason="Section 'S1' is occupied by scheduled maintenance task 'T99'.",
        conflicting_entity_id="T99",
        conflicting_entity_type="task",
    )
    cand2 = RejectedCandidate(
        candidate_start=BASE_TIME + timedelta(minutes=15),
        candidate_end=BASE_TIME + timedelta(minutes=75),
        rejection_reason="Disrupts scheduled train slot 'Express 1'.",
        conflicting_entity_id="SLOT_01",
        conflicting_entity_type="train_slot",
    )
    conflict_expl = Explanation(
        task_id="T2",
        task_name="Overhead Wire Maintenance",
        scheduled_start=BASE_TIME + timedelta(hours=2),
        scheduled_end=BASE_TIME + timedelta(hours=3),
        chosen_reason="Scheduled at 08:00 to resolve constraint competition.",
        rejected_candidates=[cand1, cand2],
    )
    formatted_conflict = format_explanation(conflict_expl)
    assert "Overhead Wire Maintenance" in formatted_conflict
    assert "Rejected alternative candidate slots:" in formatted_conflict
    assert "task T99" in formatted_conflict
    assert "train_slot SLOT_01" in formatted_conflict


def test_explanation_to_dict_serialization() -> None:
    """explanation_to_dict serializes an Explanation to a clean dictionary with ISO datetimes."""
    cand = RejectedCandidate(
        candidate_start=BASE_TIME,
        candidate_end=BASE_TIME + timedelta(minutes=45),
        rejection_reason="Track occupied.",
        conflicting_entity_id="T5",
        conflicting_entity_type="task",
    )
    expl = Explanation(
        task_id="T10",
        task_name="Ballast Tamp",
        scheduled_start=BASE_TIME + timedelta(hours=1),
        scheduled_end=BASE_TIME + timedelta(hours=1, minutes=45),
        chosen_reason="Minimizes delay penalty.",
        rejected_candidates=[cand],
    )

    d = explanation_to_dict(expl)
    assert isinstance(d, dict)
    assert d["task_id"] == "T10"
    assert d["task_name"] == "Ballast Tamp"
    assert isinstance(d["scheduled_start"], str)
    assert "2026-01-01" in d["scheduled_start"]
    assert len(d["rejected_candidates"]) == 1
    assert d["rejected_candidates"][0]["conflicting_entity_id"] == "T5"
