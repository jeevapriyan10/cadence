"""Unit and integration tests for the Ripple cascade delay-propagation simulator."""

from datetime import datetime, timedelta, timezone
import pytest

from cadence.domain.graph import NetworkGraph
from cadence.domain.schemas import (
    MaintenanceTaskSchema,
    ScheduledBlockSchema,
    TrackSectionSchema,
    TrainSlotSchema,
)
from cadence.generator import generate_synthetic_network
from cadence.profiles.registry import ProfileRegistry
from cadence.ripple import (
    RippleReport,
    TrainImpact,
    format_report_summary,
    report_to_dict,
    simulate_cascade,
)
from cadence.solve import solve_schedule

BASE_TIME = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)


def test_zero_impact_scenario() -> None:
    """A scenario with zero scheduled blocks touching any train route produces a clean zero-impact report."""
    train = TrainSlotSchema(
        id="TRAIN_1",
        name="Morning Commuter",
        scheduled_start=BASE_TIME,
        scheduled_end=BASE_TIME + timedelta(minutes=60),
        route=["S1", "S2"],
    )

    # 1. No blocks at all
    report_empty = simulate_cascade(
        scheduled_blocks=[],
        train_slots=[train],
    )
    assert report_empty.total_trains_affected == 0
    assert report_empty.total_delay_minutes == 0.0
    assert len(report_empty.per_train_impacts) == 0

    # 2. Block on a completely disjoint section S99
    disjoint_block = ScheduledBlockSchema(
        task_id="TASK_DISJOINT",
        section_id="S99",
        start_time=BASE_TIME,
        end_time=BASE_TIME + timedelta(minutes=30),
        method="full_resolve",
    )
    report_disjoint = simulate_cascade(
        scheduled_blocks=[disjoint_block],
        train_slots=[train],
    )
    assert report_disjoint.total_trains_affected == 0
    assert report_disjoint.total_delay_minutes == 0.0
    assert len(report_disjoint.per_train_impacts) == 0


def test_direct_impact_single_block() -> None:
    """A scheduled block directly overlapping a train slot's route and window produces directly_affected=True."""
    # Train occupies S1 during [06:00, 06:30] and S2 during [06:30, 07:00]
    train = TrainSlotSchema(
        id="TRAIN_DIRECT",
        name="Direct Freight",
        scheduled_start=BASE_TIME,
        scheduled_end=BASE_TIME + timedelta(hours=1),
        route=["S1", "S2"],
    )

    # Possession block on S1 from 06:10 to 06:25 (15 minutes overlap on S1)
    block = ScheduledBlockSchema(
        task_id="TASK_S1",
        section_id="S1",
        start_time=BASE_TIME + timedelta(minutes=10),
        end_time=BASE_TIME + timedelta(minutes=25),
        method="full_resolve",
    )

    report = simulate_cascade(
        scheduled_blocks=[block],
        train_slots=[train],
    )

    assert report.total_trains_affected == 1
    assert report.total_delay_minutes == 15.0
    assert len(report.per_train_impacts) == 1

    impact = report.per_train_impacts[0]
    assert impact.train_slot_id == "TRAIN_DIRECT"
    assert impact.train_name == "Direct Freight"
    assert impact.directly_affected is True
    assert impact.delay_minutes == 15.0
    assert impact.affected_sections == ["S1"]
    assert impact.cascade_source is None


def test_single_hop_cascade_propagation() -> None:
    """A train B scheduled shortly after directly-affected train A on the same section incurs a cascade delay."""
    # Train A on S1: [06:00, 07:00]
    train_a = TrainSlotSchema(
        id="TRAIN_A",
        name="Express Leader",
        scheduled_start=BASE_TIME,
        scheduled_end=BASE_TIME + timedelta(minutes=60),
        route=["S1"],
    )

    # Train B on S1: [07:10, 08:10] (Scheduled AFTER the maintenance block, so NOT directly affected)
    train_b = TrainSlotSchema(
        id="TRAIN_B",
        name="Local Follower",
        scheduled_start=BASE_TIME + timedelta(minutes=70),
        scheduled_end=BASE_TIME + timedelta(minutes=130),
        route=["S1"],
    )

    # Maintenance possession on S1 from 06:00 to 06:40 (40 mins overlap with Train A)
    # Train A is delayed by 40 mins, so its departure from S1 is shifted from 07:00 to 07:40
    # Train B arrives at 07:10, finding Train A still occupying S1 until 07:40 (30 mins conflict)
    block = ScheduledBlockSchema(
        task_id="TASK_MAINT",
        section_id="S1",
        start_time=BASE_TIME,
        end_time=BASE_TIME + timedelta(minutes=40),
        method="full_resolve",
    )

    report = simulate_cascade(
        scheduled_blocks=[block],
        train_slots=[train_a, train_b],
    )

    assert report.total_trains_affected == 2
    assert report.total_delay_minutes == 70.0  # 40.0 (Train A) + 30.0 (Train B)

    impacts_by_id = {imp.train_slot_id: imp for imp in report.per_train_impacts}
    imp_a = impacts_by_id["TRAIN_A"]
    imp_b = impacts_by_id["TRAIN_B"]

    # Train A was directly delayed
    assert imp_a.directly_affected is True
    assert imp_a.delay_minutes == 40.0
    assert imp_a.cascade_source is None

    # Train B was indirectly delayed via cascade propagation from Train A
    assert imp_b.directly_affected is False
    assert imp_b.delay_minutes == 30.0
    assert imp_b.cascade_source == "Express Leader"
    assert "S1" in imp_b.affected_sections


def test_total_delay_minutes_summation() -> None:
    """The report's total_delay_minutes strictly equals the sum of all individual train delay_minutes."""
    t1 = TrainSlotSchema(
        id="T1",
        name="Train 1",
        scheduled_start=BASE_TIME,
        scheduled_end=BASE_TIME + timedelta(minutes=60),
        route=["S1"],
    )
    t2 = TrainSlotSchema(
        id="T2",
        name="Train 2",
        scheduled_start=BASE_TIME,
        scheduled_end=BASE_TIME + timedelta(minutes=60),
        route=["S2"],
    )

    b1 = ScheduledBlockSchema(
        task_id="B1",
        section_id="S1",
        start_time=BASE_TIME,
        end_time=BASE_TIME + timedelta(minutes=20),
        method="full_resolve",
    )
    b2 = ScheduledBlockSchema(
        task_id="B2",
        section_id="S2",
        start_time=BASE_TIME + timedelta(minutes=15),
        end_time=BASE_TIME + timedelta(minutes=45),
        method="full_resolve",
    )

    report = simulate_cascade(
        scheduled_blocks=[b1, b2],
        train_slots=[t1, t2],
    )

    assert report.total_trains_affected == 2
    summed_delays = sum(imp.delay_minutes for imp in report.per_train_impacts)
    assert report.total_delay_minutes == pytest.approx(summed_delays, abs=0.01)
    assert report.total_delay_minutes == 50.0  # 20 + 30


@pytest.mark.parametrize("profile_name", ["metro", "local", "mainline"])
def test_simulate_cascade_with_solved_schedules_all_profiles(profile_name: str) -> None:
    """Run simulate_cascade on full solved schedules from solve_schedule across all network profiles."""
    profile = ProfileRegistry.get(profile_name)
    net = generate_synthetic_network(
        profile_name=profile_name,
        seed=42,
        section_count=8,
        train_count=6,
        task_count=4,
    )

    # Solve possession schedule using Module 4/5 solver
    solve_res = solve_schedule(
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
        time_horizon_minutes=1440,
        adjacencies=net["adjacencies"],
    )

    assert solve_res.status in ("OPTIMAL", "FEASIBLE")

    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])

    # Run Ripple cascade delay simulator
    report = simulate_cascade(
        scheduled_blocks=solve_res.scheduled_blocks,
        train_slots=net["train_slots"],
        graph=graph,
        sections=net["sections"],
    )

    assert isinstance(report, RippleReport)
    assert report.total_trains_affected >= 0
    assert report.total_delay_minutes >= 0.0
    assert isinstance(report.per_train_impacts, list)
    assert isinstance(report.generated_at, datetime)

    # Validate integrity of every train impact entry
    for impact in report.per_train_impacts:
        assert isinstance(impact, TrainImpact)
        assert impact.delay_minutes > 0.0
        assert len(impact.affected_sections) > 0
        if not impact.directly_affected:
            assert impact.cascade_source is not None


def test_format_report_summary_variations() -> None:
    """format_report_summary produces readable strings for zero-impact and multi-impact reports."""
    # 1. Zero impact
    zero_report = RippleReport(
        total_trains_affected=0,
        total_delay_minutes=0.0,
        per_train_impacts=[],
    )
    summary_zero = format_report_summary(zero_report)
    assert "0 trains affected" in summary_zero
    assert "no disruptions" in summary_zero

    # 2. Multi-impact
    impacts = [
        TrainImpact(
            train_slot_id="T1",
            train_name="Regional Local",
            directly_affected=True,
            delay_minutes=12.5,
            affected_sections=["S1"],
        ),
        TrainImpact(
            train_slot_id="T2",
            train_name="Intercity Express",
            directly_affected=True,
            delay_minutes=34.0,
            affected_sections=["S2"],
        ),
    ]
    multi_report = RippleReport(
        total_trains_affected=2,
        total_delay_minutes=46.5,
        per_train_impacts=impacts,
    )
    summary_multi = format_report_summary(multi_report)
    assert "2 trains affected" in summary_multi
    assert "46.5 total delay-minutes" in summary_multi
    assert "worst-hit: Intercity Express (34.0 min)" in summary_multi


def test_report_to_dict_serialization() -> None:
    """report_to_dict produces a clean, JSON-serializable dictionary with ISO timestamps."""
    impact = TrainImpact(
        train_slot_id="T1",
        train_name="Freight 99",
        directly_affected=False,
        delay_minutes=25.0,
        affected_sections=["S5"],
        cascade_source="Express 1",
    )
    report = RippleReport(
        total_trains_affected=1,
        total_delay_minutes=25.0,
        per_train_impacts=[impact],
        generated_at=BASE_TIME,
    )

    data = report_to_dict(report)
    assert isinstance(data, dict)
    assert data["total_trains_affected"] == 1
    assert data["total_delay_minutes"] == 25.0
    assert len(data["per_train_impacts"]) == 1
    assert data["per_train_impacts"][0]["train_name"] == "Freight 99"
    assert data["per_train_impacts"][0]["cascade_source"] == "Express 1"
    assert isinstance(data["generated_at"], str)
    assert "2026-01-01" in data["generated_at"]
