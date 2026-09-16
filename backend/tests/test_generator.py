"""Unit tests for the Cadence synthetic railway network generator."""

from datetime import timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cadence.domain.db import Base
from cadence.domain.graph import NetworkGraph
from cadence.domain.models import (
    MaintenanceTask,
    SectionAdjacency,
    TrackSection,
    TrainSlot,
)
from cadence.domain.schemas import TrainSlotSchema
from cadence.generator import (
    generate_linear_topology,
    generate_linear_with_passing_loops_topology,
    generate_loop_topology,
    generate_maintenance_tasks,
    generate_synthetic_network,
    generate_train_slots,
    persist_to_db,
)
from cadence.profiles.registry import ProfileRegistry


@pytest.fixture
def db_session() -> Session:
    """Provide an isolated in-memory SQLite database session for generator tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    testing_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_reproducibility_identical_seed() -> None:
    """generate_synthetic_network called twice with identical seed produces identical outputs."""
    net1 = generate_synthetic_network("metro", seed=42)
    net2 = generate_synthetic_network("metro", seed=42)

    # Compare section ids, lengths, attributes
    assert len(net1["sections"]) == len(net2["sections"])
    assert [s.id for s in net1["sections"]] == [s.id for s in net2["sections"]]
    assert [s.length_meters for s in net1["sections"]] == [s.length_meters for s in net2["sections"]]
    assert [s.attributes for s in net1["sections"]] == [s.attributes for s in net2["sections"]]

    # Compare adjacency structure
    assert len(net1["adjacencies"]) == len(net2["adjacencies"])
    assert [
        (a.section_a_id, a.section_b_id) for a in net1["adjacencies"]
    ] == [
        (a.section_a_id, a.section_b_id) for a in net2["adjacencies"]
    ]

    # Compare train slot counts, ids, routes, and scheduled times
    assert len(net1["train_slots"]) == len(net2["train_slots"])
    assert [t.id for t in net1["train_slots"]] == [t.id for t in net2["train_slots"]]
    assert [t.route for t in net1["train_slots"]] == [t.route for t in net2["train_slots"]]
    assert [t.scheduled_start for t in net1["train_slots"]] == [t.scheduled_start for t in net2["train_slots"]]
    assert [t.scheduled_end for t in net1["train_slots"]] == [t.scheduled_end for t in net2["train_slots"]]
    assert [t.priority for t in net1["train_slots"]] == [t.priority for t in net2["train_slots"]]

    # Compare maintenance task counts, sections, durations, windows
    assert len(net1["maintenance_tasks"]) == len(net2["maintenance_tasks"])
    assert [m.id for m in net1["maintenance_tasks"]] == [m.id for m in net2["maintenance_tasks"]]
    assert [m.section_id for m in net1["maintenance_tasks"]] == [m.section_id for m in net2["maintenance_tasks"]]
    assert [m.duration_minutes for m in net1["maintenance_tasks"]] == [m.duration_minutes for m in net2["maintenance_tasks"]]
    assert [m.earliest_start for m in net1["maintenance_tasks"]] == [m.earliest_start for m in net2["maintenance_tasks"]]
    assert [m.latest_end for m in net1["maintenance_tasks"]] == [m.latest_end for m in net2["maintenance_tasks"]]
    assert [m.is_emergency for m in net1["maintenance_tasks"]] == [m.is_emergency for m in net2["maintenance_tasks"]]


def test_different_seeds_produce_different_networks() -> None:
    """generate_synthetic_network with different seeds produces different networks."""
    net_a = generate_synthetic_network("metro", seed=42)
    net_b = generate_synthetic_network("metro", seed=99)

    # Different section lengths
    lengths_a = [s.length_meters for s in net_a["sections"]]
    lengths_b = [s.length_meters for s in net_b["sections"]]
    assert lengths_a != lengths_b

    # Different train slot start times or routes
    starts_a = [t.scheduled_start for t in net_a["train_slots"]]
    starts_b = [t.scheduled_start for t in net_b["train_slots"]]
    assert starts_a != starts_b

    # Different task assignments or durations
    tasks_a = [(m.section_id, m.duration_minutes) for m in net_a["maintenance_tasks"]]
    tasks_b = [(m.section_id, m.duration_minutes) for m in net_b["maintenance_tasks"]]
    assert tasks_a != tasks_b


def test_metro_topology_expectations() -> None:
    """Metro profile generates a single connected loop where all nodes have degree 2."""
    net = generate_synthetic_network("metro", seed=42)
    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])

    # Graph is a single connected component
    assert graph.is_connected() is True

    # Every node in a simple ring has degree 2
    for sec in net["sections"]:
        neighbors = graph.get_neighbors(sec.id)
        assert len(neighbors) == 2, f"Section {sec.id} expected degree 2, got {len(neighbors)}"


def test_mainline_topology_expectations() -> None:
    """Mainline profile generates a linear backbone with passing loop alternate paths."""
    net = generate_synthetic_network("mainline", seed=42)
    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])

    assert graph.is_connected() is True

    # Mainline graph has at least one section with a passing-loop alternate path
    # findable via NetworkGraph.shortest_path having more than one viable route
    found_passing_loop_alt_path = False
    for sec in net["sections"]:
        neighbors = graph.get_neighbors(sec.id)
        if len(neighbors) >= 2:
            # Simulate removal of this section
            subgraph = graph.graph.copy()
            subgraph.remove_node(sec.id)
            sim = NetworkGraph(subgraph)

            # Check if an alternate path still connects its neighbors
            for i in range(len(neighbors)):
                for j in range(i + 1, len(neighbors)):
                    alt_path = sim.shortest_path(neighbors[i], neighbors[j])
                    if len(alt_path) >= 2:
                        found_passing_loop_alt_path = True
                        break
                if found_passing_loop_alt_path:
                    break
        if found_passing_loop_alt_path:
            break

    assert found_passing_loop_alt_path is True, "Expected at least one section with a passing loop alternate path"


def test_local_topology_expectations() -> None:
    """Local profile generates a linear-ish graph with express/local tags present."""
    net = generate_synthetic_network("local", seed=42)
    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])

    assert graph.is_connected() is True

    # Check linear structure: exactly 2 end nodes of degree 1, all intermediate nodes degree 2
    degrees = [len(graph.get_neighbors(s.id)) for s in net["sections"]]
    assert degrees.count(1) == 2
    assert all(d in (1, 2) for d in degrees)

    # Check express/local tags on track sections
    mixed_sections = [s for s in net["sections"] if s.attributes.get("has_express_and_local")]
    assert len(mixed_sections) >= 1, "Expected at least one section with express and local tags"

    # Check express/local tags on train slots
    train_types = {t.attributes.get("train_type") for t in net["train_slots"]}
    assert "express" in train_types
    assert "local" in train_types


@pytest.mark.parametrize("profile_name", ["metro", "mainline", "local"])
def test_train_slots_headway_respect(profile_name: str) -> None:
    """Train slots generated on overlapping routes respect the profile's min_headway_minutes."""
    profile = ProfileRegistry.get(profile_name)
    net = generate_synthetic_network(profile_name, seed=42, train_count=15)
    train_slots = net["train_slots"]

    for i in range(len(train_slots)):
        slot_a = train_slots[i]
        schema_a = TrainSlotSchema.model_validate(slot_a)
        for j in range(i + 1, len(train_slots)):
            slot_b = train_slots[j]
            shared_sections = set(slot_a.route) & set(slot_b.route)
            if not shared_sections:
                continue

            schema_b = TrainSlotSchema.model_validate(slot_b)
            min_headway = profile.min_headway_minutes(schema_a, schema_b)

            # Scheduled start time separation check
            start_gap = abs((slot_a.scheduled_start - slot_b.scheduled_start).total_seconds()) / 60.0
            assert start_gap >= min_headway, (
                f"Headway violation between {slot_a.name} and {slot_b.name} on profile {profile_name}: "
                f"gap={start_gap}m < min_headway={min_headway}m"
            )

            # Section entry time separation check on every shared section
            for sec in shared_sections:
                entry_a = slot_a.scheduled_start + timedelta(minutes=slot_a.route.index(sec) * 3)
                entry_b = slot_b.scheduled_start + timedelta(minutes=slot_b.route.index(sec) * 3)
                sec_gap = abs((entry_a - entry_b).total_seconds()) / 60.0
                assert sec_gap >= min_headway, (
                    f"Headway violation on section {sec} between {slot_a.name} and {slot_b.name}: "
                    f"gap={sec_gap}m < min_headway={min_headway}m"
                )


def test_persist_to_db_roundtrip(db_session: Session) -> None:
    """persist_to_db correctly persists and round-trips a synthetic network in SQLite."""
    net = generate_synthetic_network("metro", seed=42, section_count=8, train_count=6, task_count=4)
    persist_to_db(net, db_session)

    # Verify TrackSections
    saved_sections = db_session.query(TrackSection).all()
    assert len(saved_sections) == 8
    assert all(s.id.startswith("SEC-") for s in saved_sections)

    # Verify SectionAdjacencies
    saved_adjs = db_session.query(SectionAdjacency).all()
    assert len(saved_adjs) == 8
    for adj in saved_adjs:
        assert adj.section_a is not None
        assert adj.section_b is not None

    # Verify TrainSlots
    saved_slots = db_session.query(TrainSlot).all()
    assert len(saved_slots) == 6
    for slot in saved_slots:
        assert len(slot.route) >= 2
        assert slot.scheduled_end > slot.scheduled_start

    # Verify MaintenanceTasks
    saved_tasks = db_session.query(MaintenanceTask).all()
    assert len(saved_tasks) == 4
    for task in saved_tasks:
        assert task.section is not None
        assert task.duration_minutes > 0
        assert task.latest_end >= task.earliest_start + timedelta(minutes=task.duration_minutes)
