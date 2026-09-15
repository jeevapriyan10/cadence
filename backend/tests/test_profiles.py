"""Comprehensive unit tests for railway network profile subsystem."""

from datetime import datetime, timedelta, timezone
import pytest

from cadence.domain.graph import NetworkGraph
from cadence.domain.models import SectionAdjacency, TrackSection
from cadence.domain.schemas import MaintenanceTaskSchema, TrainSlotSchema
from cadence.profiles import (
    LocalProfile,
    MainlineProfile,
    MetroProfile,
    NetworkProfile,
    ProfileRegistry,
)


def _make_train_slot(
    name: str = "Slot-1",
    route: list[str] | None = None,
    priority: int = 1,
    attributes: dict | None = None,
) -> TrainSlotSchema:
    now = datetime.now(timezone.utc)
    return TrainSlotSchema(
        name=name,
        scheduled_start=now,
        scheduled_end=now + timedelta(hours=1),
        route=route or ["S1", "S2"],
        priority=priority,
        attributes=attributes or {},
    )


def _make_task(
    name: str = "Task-1",
    section_id: str = "S1",
    priority: int = 1,
    is_emergency: bool = False,
    attributes: dict | None = None,
) -> MaintenanceTaskSchema:
    now = datetime.now(timezone.utc)
    return MaintenanceTaskSchema(
        name=name,
        section_id=section_id,
        duration_minutes=60,
        earliest_start=now,
        latest_end=now + timedelta(hours=4),
        priority=priority,
        is_emergency=is_emergency,
        attributes=attributes or {},
    )


def test_network_profile_is_true_abc() -> None:
    """NetworkProfile is a true ABC — instantiating it directly raises TypeError."""
    with pytest.raises(TypeError) as exc_info:
        NetworkProfile()  # type: ignore[abstract]
    assert "abstract" in str(exc_info.value).lower()


def test_profiles_instantiation_and_interface() -> None:
    """Each of the three profiles can be instantiated and satisfies the NetworkProfile interface."""
    profiles = [MetroProfile(), LocalProfile(), MainlineProfile()]

    slot_a = _make_train_slot("Slot A")
    slot_b = _make_train_slot("Slot B")
    task = _make_task("Inspection")

    # Minimal 2-node graph for interface validation
    sections = [TrackSection(id="A", name="A"), TrackSection(id="B", name="B")]
    adjs = [SectionAdjacency(section_a_id="A", section_b_id="B")]
    graph = NetworkGraph.build_from_sections(sections, adjs)

    for profile in profiles:
        assert isinstance(profile, NetworkProfile)
        assert isinstance(profile.profile_name, str) and len(profile.profile_name) > 0
        assert isinstance(profile.default_topology, str) and len(profile.default_topology) > 0

        # Verify none of the abstract methods raise NotImplementedError
        safe, reason = profile.is_safe_adjacency(graph, "A", "A")
        assert isinstance(safe, bool)
        assert isinstance(reason, str) and len(reason) > 0

        headway = profile.min_headway_minutes(slot_a, slot_b)
        assert isinstance(headway, int)
        assert headway > 0

        weight = profile.priority_weight(task)
        assert isinstance(weight, float)
        assert weight > 0.0

        penalty = profile.disruption_penalty(slot_a)
        assert isinstance(penalty, float)
        assert penalty > 0.0

        hint = profile.default_topology_generator_hint()
        assert isinstance(hint, dict)
        assert "topology_type" in hint


def test_metro_is_safe_adjacency_loop_disconnect() -> None:
    """MetroProfile flags an unsafe case on a small loop when blocking disconnects it, and safe when it remains connected."""
    # Small loop: A - B - C - D - A
    sections = [
        TrackSection(id="A", name="Sec A"),
        TrackSection(id="B", name="Sec B"),
        TrackSection(id="C", name="Sec C"),
        TrackSection(id="D", name="Sec D"),
    ]
    adjs = [
        SectionAdjacency(section_a_id="A", section_b_id="B"),
        SectionAdjacency(section_a_id="B", section_b_id="C"),
        SectionAdjacency(section_a_id="C", section_b_id="D"),
        SectionAdjacency(section_a_id="D", section_b_id="A"),
    ]
    loop_graph = NetworkGraph.build_from_sections(sections, adjs)
    metro = MetroProfile()

    # Safe case: blocking only one section (A) leaves B - C - D connected
    safe, reason = metro.is_safe_adjacency(loop_graph, "A", "A")
    assert safe is True
    assert "operational" in reason.lower() or "safe" in reason.lower()

    # Unsafe case: blocking opposite sections A and C partitions graph into {B} and {D}
    unsafe, reason_unsafe = metro.is_safe_adjacency(loop_graph, "A", "C")
    assert unsafe is False
    assert "loop connectivity" in reason_unsafe.lower() or "unsafe" in reason_unsafe.lower()


def test_mainline_is_safe_adjacency_passing_loop() -> None:
    """MainlineProfile correctly detects a stranded passing loop scenario vs a safe case with alternate path."""
    # Linear graph with passing loop:
    # J1 connects to M1 (mainline) and L1 (loop siding)
    # M1 and L1 connect to J2
    sections = [
        TrackSection(id="J1", name="Junction 1"),
        TrackSection(id="M1", name="Mainline Path"),
        TrackSection(id="L1", name="Passing Loop"),
        TrackSection(id="J2", name="Junction 2"),
    ]
    adjs = [
        SectionAdjacency(section_a_id="J1", section_b_id="M1"),
        SectionAdjacency(section_a_id="M1", section_b_id="J2"),
        SectionAdjacency(section_a_id="J1", section_b_id="L1"),
        SectionAdjacency(section_a_id="L1", section_b_id="J2"),
    ]
    mainline_graph = NetworkGraph.build_from_sections(sections, adjs)
    mainline = MainlineProfile()

    # Safe case: blocking only M1 leaves alternate route through passing loop L1
    safe, reason_safe = mainline.is_safe_adjacency(mainline_graph, "M1", "M1")
    assert safe is True
    assert "safe" in reason_safe.lower() or "alternate" in reason_safe.lower()

    # Unsafe case: blocking both M1 and L1 strands the passing loop, removing the only alternate path between J1 and J2
    unsafe, reason_unsafe = mainline.is_safe_adjacency(mainline_graph, "M1", "L1")
    assert unsafe is False
    assert "strands passing loop" in reason_unsafe.lower() or "no alternate path" in reason_unsafe.lower()


def test_local_is_safe_adjacency_mixed_services() -> None:
    """LocalProfile flags unsafe if blocking sections isolates any section serving both express and local trains."""
    # Linear graph: S1 - S2 - S3 - S4 - S5
    sections = [
        TrackSection(id="S1", name="Station 1"),
        TrackSection(id="S2", name="Section 2"),
        TrackSection(id="S3", name="Station 3 (Express & Local)", attributes={"has_express_and_local": True}),
        TrackSection(id="S4", name="Section 4"),
        TrackSection(id="S5", name="Station 5"),
    ]
    adjs = [
        SectionAdjacency(section_a_id="S1", section_b_id="S2"),
        SectionAdjacency(section_a_id="S2", section_b_id="S3"),
        SectionAdjacency(section_a_id="S3", section_b_id="S4"),
        SectionAdjacency(section_a_id="S4", section_b_id="S5"),
    ]
    local_graph = NetworkGraph.build_from_sections(sections, adjs)
    local = LocalProfile()

    # Blocking S2 and S4 isolates S3 completely (degree 0 in remaining active graph) -> Unsafe
    unsafe, reason_unsafe = local.is_safe_adjacency(local_graph, "S2", "S4")
    assert unsafe is False
    assert "isolates" in reason_unsafe.lower() and "S3" in reason_unsafe

    # Blocking S1 and S2 leaves S3 connected to S4 and S5 -> Safe
    safe, reason_safe = local.is_safe_adjacency(local_graph, "S1", "S2")
    assert safe is True
    assert "safe" in reason_safe.lower()


def test_headway_hierarchy_metro_local_mainline() -> None:
    """min_headway_minutes returns sensibly different values with Metro < Local < Mainline."""
    metro = MetroProfile()
    local = LocalProfile()
    mainline = MainlineProfile()

    slot_a = _make_train_slot("Slot A")
    slot_b = _make_train_slot("Slot B")

    metro_headway = metro.min_headway_minutes(slot_a, slot_b)
    local_headway = local.min_headway_minutes(slot_a, slot_b)
    mainline_headway = mainline.min_headway_minutes(slot_a, slot_b)

    # Metro is the tightest, followed by Local, with Mainline the longest
    assert metro_headway < local_headway < mainline_headway
    assert 3 <= metro_headway <= 5
    assert 6 <= local_headway <= 10
    assert 15 <= mainline_headway <= 20


def test_priority_weight_emergency_dominates_all_profiles() -> None:
    """priority_weight for an is_emergency=True task is strictly higher than non-emergency across all profiles."""
    profiles = [MetroProfile(), LocalProfile(), MainlineProfile()]

    routine_task = _make_task("Routine Track Work", priority=3, is_emergency=False)
    emergency_task = _make_task("Broken Rail Emergency", priority=1, is_emergency=True)

    for profile in profiles:
        routine_weight = profile.priority_weight(routine_task)
        emergency_weight = profile.priority_weight(emergency_task)
        assert emergency_weight > routine_weight, f"{profile.profile_name} failed emergency dominance"


def test_mainline_priority_and_disruption_passenger_scaling() -> None:
    """MainlineProfile weights passenger tasks higher and scales disruption penalties for passenger slots."""
    mainline = MainlineProfile()

    passenger_task = _make_task("Signal Work", priority=2, is_emergency=False, attributes={"traffic_type": "passenger"})
    freight_task = _make_task("Siding Work", priority=2, is_emergency=False, attributes={"traffic_type": "freight"})
    assert mainline.priority_weight(passenger_task) > mainline.priority_weight(freight_task)

    passenger_slot = _make_train_slot("InterCity Express", priority=2, attributes={"train_type": "passenger"})
    freight_slot = _make_train_slot("Heavy Freight", priority=2, attributes={"train_type": "freight"})
    assert mainline.disruption_penalty(passenger_slot) > mainline.disruption_penalty(freight_slot)


def test_profile_registry() -> None:
    """ProfileRegistry retrieves profiles by name and raises clear error for unregistered names."""
    available = ProfileRegistry.list_available()
    assert "metro" in available
    assert "local" in available
    assert "mainline" in available

    metro = ProfileRegistry.get("metro")
    assert isinstance(metro, MetroProfile)
    assert metro.profile_name == "metro"

    local = ProfileRegistry.get("local")
    assert isinstance(local, LocalProfile)
    assert local.profile_name == "local"

    mainline = ProfileRegistry.get("mainline")
    assert isinstance(mainline, MainlineProfile)
    assert mainline.profile_name == "mainline"

    # Case insensitivity test
    assert isinstance(ProfileRegistry.get("METRO"), MetroProfile)

    # Unregistered name error
    with pytest.raises(KeyError) as exc_info:
        ProfileRegistry.get("hyperloop")
    assert "Unknown network profile 'hyperloop'" in str(exc_info.value)
    assert "metro" in str(exc_info.value)
