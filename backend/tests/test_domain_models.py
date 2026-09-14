"""Unit tests for Cadence base domain models, schemas, graph, and db operations."""

from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cadence.domain.db import Base
from cadence.domain.graph import NetworkGraph
from cadence.domain.models import (
    MaintenanceTask,
    ScheduledBlock,
    SectionAdjacency,
    TrackSection,
    TrainSlot,
)
from cadence.domain.schemas import (
    MaintenanceTaskSchema,
    ScheduledBlockSchema,
    SectionAdjacencySchema,
    TrackSectionSchema,
    TrainSlotSchema,
)


@pytest.fixture
def db_session() -> Session:
    """Provide an in-memory SQLite database session for unit tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_persist_and_retrieve_domain_models(db_session: Session) -> None:
    """Verify persisting and retrieving TrackSection, TrainSlot, MaintenanceTask, and ScheduledBlock."""
    # 1. Create and persist TrackSection
    section = TrackSection(
        id="SEC-01",
        name="Platform 1 Track",
        length_meters=450.5,
        attributes={"electrified": True, "gauge_mm": 1435},
    )
    db_session.add(section)
    db_session.commit()

    retrieved_section = db_session.query(TrackSection).filter_by(id="SEC-01").first()
    assert retrieved_section is not None
    assert retrieved_section.name == "Platform 1 Track"
    assert retrieved_section.length_meters == 450.5
    assert retrieved_section.attributes["electrified"] is True

    # Validate ORM-to-Pydantic schema conversion
    section_schema = TrackSectionSchema.model_validate(retrieved_section)
    assert section_schema.id == "SEC-01"
    assert section_schema.length_meters == 450.5

    # 2. Create and persist TrainSlot
    now = datetime.now(timezone.utc)
    train_slot = TrainSlot(
        id="SLOT-101",
        name="Express 101",
        scheduled_start=now,
        scheduled_end=now + timedelta(hours=2),
        route=["SEC-01", "SEC-02"],
        priority=3,
        attributes={"train_type": "high_speed"},
    )
    db_session.add(train_slot)
    db_session.commit()

    retrieved_slot = db_session.query(TrainSlot).filter_by(id="SLOT-101").first()
    assert retrieved_slot is not None
    assert retrieved_slot.name == "Express 101"
    assert retrieved_slot.route == ["SEC-01", "SEC-02"]
    assert retrieved_slot.priority == 3

    slot_schema = TrainSlotSchema.model_validate(retrieved_slot)
    assert slot_schema.name == "Express 101"

    # 3. Create and persist MaintenanceTask
    task = MaintenanceTask(
        id="TASK-901",
        name="Ballast Tamping",
        section_id="SEC-01",
        duration_minutes=120,
        earliest_start=now + timedelta(days=1),
        latest_end=now + timedelta(days=1, hours=8),
        priority=2,
        is_emergency=False,
        attributes={"crew": "Alpha-Team"},
    )
    db_session.add(task)
    db_session.commit()

    retrieved_task = db_session.query(MaintenanceTask).filter_by(id="TASK-901").first()
    assert retrieved_task is not None
    assert retrieved_task.name == "Ballast Tamping"
    assert retrieved_task.duration_minutes == 120
    assert retrieved_task.is_emergency is False
    assert retrieved_task.section.name == "Platform 1 Track"

    task_schema = MaintenanceTaskSchema.model_validate(retrieved_task)
    assert task_schema.duration_minutes == 120

    # 4. Create and persist ScheduledBlock
    block = ScheduledBlock(
        id="BLOCK-5001",
        task_id="TASK-901",
        section_id="SEC-01",
        start_time=now + timedelta(days=1, hours=2),
        end_time=now + timedelta(days=1, hours=4),
        method="full_resolve",
    )
    db_session.add(block)
    db_session.commit()

    retrieved_block = db_session.query(ScheduledBlock).filter_by(id="BLOCK-5001").first()
    assert retrieved_block is not None
    assert retrieved_block.task_id == "TASK-901"
    assert retrieved_block.method == "full_resolve"
    assert retrieved_block.task.name == "Ballast Tamping"

    block_schema = ScheduledBlockSchema.model_validate(retrieved_block)
    assert block_schema.id == "BLOCK-5001"
    assert block_schema.method == "full_resolve"


def test_network_graph_adjacency_and_neighbors() -> None:
    """Test NetworkGraph.are_adjacent, get_neighbors, and shortest_path on a 4-node graph: A - B - C - D."""
    sections = [
        TrackSection(id="A", name="Section A"),
        TrackSection(id="B", name="Section B"),
        TrackSection(id="C", name="Section C"),
        TrackSection(id="D", name="Section D"),
    ]
    adjacencies = [
        SectionAdjacency(id="e1", section_a_id="A", section_b_id="B"),
        SectionAdjacency(id="e2", section_a_id="B", section_b_id="C"),
        SectionAdjacency(id="e3", section_a_id="C", section_b_id="D"),
    ]

    graph = NetworkGraph.build_from_sections(sections, adjacencies)

    # Test adjacency checks
    assert graph.are_adjacent("A", "B") is True
    assert graph.are_adjacent("B", "A") is True
    assert graph.are_adjacent("B", "C") is True
    assert graph.are_adjacent("C", "D") is True
    assert graph.are_adjacent("A", "C") is False
    assert graph.are_adjacent("A", "D") is False

    # Test get_neighbors
    assert set(graph.get_neighbors("B")) == {"A", "C"}
    assert graph.get_neighbors("A") == ["B"]
    assert graph.get_neighbors("D") == ["C"]
    assert graph.get_neighbors("NON_EXISTENT") == []

    # Test shortest path
    assert graph.shortest_path("A", "D") == ["A", "B", "C", "D"]
    assert graph.shortest_path("B", "C") == ["B", "C"]
    assert graph.shortest_path("A", "NON_EXISTENT") == []


def test_network_graph_is_connected() -> None:
    """Test NetworkGraph.is_connected correctly detects connected and disconnected topologies."""
    # Disconnected graph: two disconnected components {A, B} and {C, D}
    sections = [
        TrackSection(id="A", name="Section A"),
        TrackSection(id="B", name="Section B"),
        TrackSection(id="C", name="Section C"),
        TrackSection(id="D", name="Section D"),
    ]
    disconnected_adjacencies = [
        SectionAdjacency(id="e1", section_a_id="A", section_b_id="B"),
        SectionAdjacency(id="e2", section_a_id="C", section_b_id="D"),
    ]
    disconnected_graph = NetworkGraph.build_from_sections(sections, disconnected_adjacencies)
    assert disconnected_graph.is_connected() is False
    assert disconnected_graph.shortest_path("A", "D") == []

    # Now bridge the gap by connecting B and C
    connected_adjacencies = [
        SectionAdjacency(id="e1", section_a_id="A", section_b_id="B"),
        SectionAdjacency(id="e2", section_a_id="C", section_b_id="D"),
        SectionAdjacency(id="e3", section_a_id="B", section_b_id="C"),
    ]
    connected_graph = NetworkGraph.build_from_sections(sections, connected_adjacencies)
    assert connected_graph.is_connected() is True
    assert connected_graph.shortest_path("A", "D") == ["A", "B", "C", "D"]

    # Empty graph
    empty_graph = NetworkGraph()
    assert empty_graph.is_connected() is False


def test_pydantic_schema_validation() -> None:
    """Verify Pydantic schema validation rejects invalid data such as negative duration_minutes."""
    now = datetime.now(timezone.utc)

    # Valid task schema passes
    valid_task = MaintenanceTaskSchema(
        id="TASK-OK",
        name="Valid Inspection",
        section_id="SEC-01",
        duration_minutes=60,
        earliest_start=now,
        latest_end=now + timedelta(hours=4),
        priority=1,
    )
    assert valid_task.duration_minutes == 60

    # Negative duration_minutes must be rejected with ValidationError
    with pytest.raises(ValidationError) as exc_info:
        MaintenanceTaskSchema(
            id="TASK-FAIL",
            name="Invalid Task",
            section_id="SEC-01",
            duration_minutes=-30,
            earliest_start=now,
            latest_end=now + timedelta(hours=4),
        )
    assert "duration_minutes" in str(exc_info.value)

    # Negative priority in TrainSlotSchema must also be rejected
    with pytest.raises(ValidationError) as exc_info_slot:
        TrainSlotSchema(
            id="SLOT-FAIL",
            name="Negative Priority Slot",
            scheduled_start=now,
            scheduled_end=now + timedelta(hours=1),
            priority=-1,
        )
    assert "priority" in str(exc_info_slot.value)
