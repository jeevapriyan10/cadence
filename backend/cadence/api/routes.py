"""FastAPI HTTP route definitions for Cadence Track/Solve/Ripple/Reason engine."""

from typing import Any
from fastapi import APIRouter, HTTPException, status

from cadence.api.schemas import (
    ErrorResponse,
    ExplainAllResponse,
    ExplainResponse,
    GenerateNetworkResponse,
    GenerateNetworkRequest,
    RippleResponse,
    SolveRequest,
    SolveResponse,
)
from cadence.api.store import run_store
from cadence.domain.graph import NetworkGraph
from cadence.domain.schemas import (
    MaintenanceTaskSchema,
    SectionAdjacencySchema,
    TrackSectionSchema,
    TrainSlotSchema,
)
from cadence.generator import generate_synthetic_network
from cadence.profiles.registry import ProfileRegistry
from cadence.reason import (
    explain_full_schedule,
    explain_task_scheduling,
    explanation_to_dict,
    format_explanation,
)
from cadence.ripple import format_report_summary, simulate_cascade
from cadence.solve import build_cp_model, solve_schedule

router = APIRouter()


@router.get(
    "/health",
    summary="Health Check",
    tags=["System"],
)
async def health_check() -> dict[str, str]:
    """Trivial health check verifying that the Cadence API is running and responsive."""
    return {"status": "ok"}


@router.post(
    "/networks/generate",
    response_model=GenerateNetworkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate Synthetic Network",
    tags=["Track"],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid profile or generation parameters"},
    },
)
async def generate_network(request: GenerateNetworkRequest) -> GenerateNetworkResponse:
    """Generate a reproducible synthetic railway network shaped by a NetworkProfile.

    Builds topology (sections & adjacencies), timetable train slots, and maintenance
    possession tasks, then caches the generated dataset in the active RunStore.
    """
    try:
        net = generate_synthetic_network(
            profile_name=request.profile_name,
            seed=request.seed,
            section_count=request.section_count,
            train_count=request.train_count,
            task_count=request.task_count,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown network profile '{request.profile_name}'. Registered profiles: {ProfileRegistry.list_available()}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Network generation failed: {exc}",
        ) from exc

    run_id = run_store.create_run(network_data=net)

    return GenerateNetworkResponse(
        run_id=run_id,
        profile_name=net["profile_name"],
        seed=net["seed"],
        section_count=len(net["sections"]),
        train_count=len(net["train_slots"]),
        task_count=len(net["maintenance_tasks"]),
    )


@router.get(
    "/networks/{run_id}",
    summary="Get Network Details",
    tags=["Track"],
    responses={
        404: {"model": ErrorResponse, "description": "Run ID not found"},
    },
)
async def get_network(run_id: str) -> dict[str, Any]:
    """Retrieve full details of a generated railway network by run_id."""
    run_data = run_store.get_run(run_id)
    if run_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    net = run_data.get("network")
    if net is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No network data found for run '{run_id}'.",
        )

    # Serialize domain entities to clean JSON-serializable dictionaries
    sections_json = [
        TrackSectionSchema.model_validate(s).model_dump(mode="json") for s in net["sections"]
    ]
    adjacencies_json = [
        SectionAdjacencySchema.model_validate(a).model_dump(mode="json") for a in net["adjacencies"]
    ]
    train_slots_json = [
        TrainSlotSchema.model_validate(t).model_dump(mode="json") for t in net["train_slots"]
    ]
    tasks_json = [
        MaintenanceTaskSchema.model_validate(m).model_dump(mode="json") for m in net["maintenance_tasks"]
    ]

    return {
        "run_id": run_id,
        "profile_name": net["profile_name"],
        "seed": net["seed"],
        "sections": sections_json,
        "adjacencies": adjacencies_json,
        "train_slots": train_slots_json,
        "maintenance_tasks": tasks_json,
    }


@router.post(
    "/solve",
    response_model=SolveResponse,
    summary="Solve Block Scheduling Problem",
    tags=["Solve"],
    responses={
        400: {"model": ErrorResponse, "description": "No network stored for this run"},
        404: {"model": ErrorResponse, "description": "Run ID not found"},
    },
)
async def solve(request: SolveRequest) -> SolveResponse:
    """Solve the maintenance possession block scheduling problem using Google OR-Tools CP-SAT.

    Enforces structural same-section and safety-adjacency hard constraints, optimizes
    priority weights and train disruption penalties, and stores the resulting schedule.
    """
    run_data = run_store.get_run(request.run_id)
    if run_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{request.run_id}' not found.",
        )

    net = run_data.get("network")
    if net is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run '{request.run_id}' has no stored network. Generate a network first.",
        )

    profile = ProfileRegistry.get(net["profile_name"])

    solve_result = solve_schedule(
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
        time_horizon_minutes=request.time_horizon_minutes,
        time_limit_seconds=request.time_limit_seconds,
        adjacencies=net["adjacencies"],
    )

    # Build model context to support post-hoc explanation in Reason
    model_context = build_cp_model(
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
        time_horizon_minutes=request.time_horizon_minutes,
        adjacencies=net["adjacencies"],
    )

    run_store.update_run(
        request.run_id,
        solve_result=solve_result,
        model_context=model_context,
    )

    blocks_json = [b.model_dump(mode="json") for b in solve_result.scheduled_blocks]

    return SolveResponse(
        run_id=request.run_id,
        status=solve_result.status,
        scheduled_blocks=blocks_json,
        objective_value=solve_result.objective_value,
        wall_time_seconds=solve_result.wall_time_seconds,
    )


@router.get(
    "/ripple/{run_id}",
    response_model=RippleResponse,
    summary="Simulate Downstream Cascade Delays",
    tags=["Ripple"],
    responses={
        400: {"model": ErrorResponse, "description": "No solve result found for this run"},
        404: {"model": ErrorResponse, "description": "Run ID not found"},
    },
)
async def get_ripple(run_id: str) -> RippleResponse:
    """Simulate downstream cascade delay propagation for a solved schedule using Ripple."""
    run_data = run_store.get_run(run_id)
    if run_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    solve_result = run_data.get("solve_result")
    if solve_result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No solve has been executed for run '{run_id}' yet. Run /solve first.",
        )

    net = run_data["network"]
    graph = NetworkGraph.build_from_sections(net["sections"], net["adjacencies"])

    report = simulate_cascade(
        scheduled_blocks=solve_result.scheduled_blocks,
        train_slots=net["train_slots"],
        graph=graph,
        sections=net["sections"],
    )

    run_store.update_run(run_id, ripple_report=report)
    summary_text = format_report_summary(report)

    impacts_json = [imp.model_dump(mode="json") for imp in report.per_train_impacts]

    return RippleResponse(
        run_id=run_id,
        total_trains_affected=report.total_trains_affected,
        total_delay_minutes=report.total_delay_minutes,
        per_train_impacts=impacts_json,
        summary=summary_text,
    )


@router.get(
    "/reason/{run_id}/{task_id}",
    response_model=ExplainResponse,
    summary="Explain Task Scheduling",
    tags=["Reason"],
    responses={
        400: {"model": ErrorResponse, "description": "No solve result found for this run"},
        404: {"model": ErrorResponse, "description": "Run ID or Task ID not found"},
    },
)
async def get_task_explanation(run_id: str, task_id: str) -> ExplainResponse:
    """Reconstruct post-hoc why a specific maintenance task was scheduled in its slot and why alternatives were rejected."""
    run_data = run_store.get_run(run_id)
    if run_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    solve_result = run_data.get("solve_result")
    if solve_result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No solve has been executed for run '{run_id}' yet. Run /solve first.",
        )

    net = run_data["network"]
    scheduled_task_ids = {b.task_id for b in solve_result.scheduled_blocks}
    if task_id not in scheduled_task_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' was not scheduled in run '{run_id}'.",
        )

    profile = ProfileRegistry.get(net["profile_name"])
    model_context = run_data.get("model_context") or {}

    explanation = explain_task_scheduling(
        task_id=task_id,
        solve_result=solve_result,
        model_context=model_context,
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
    )

    return ExplainResponse(
        run_id=run_id,
        task_id=task_id,
        explanation=explanation_to_dict(explanation),
        summary=format_explanation(explanation),
    )


@router.get(
    "/reason/{run_id}",
    response_model=ExplainAllResponse,
    summary="Explain Full Schedule",
    tags=["Reason"],
    responses={
        400: {"model": ErrorResponse, "description": "No solve result found for this run"},
        404: {"model": ErrorResponse, "description": "Run ID not found"},
    },
)
async def get_full_schedule_explanation(run_id: str) -> ExplainAllResponse:
    """Generate post-hoc explanations for every scheduled maintenance possession task in a run."""
    run_data = run_store.get_run(run_id)
    if run_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    solve_result = run_data.get("solve_result")
    if solve_result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No solve has been executed for run '{run_id}' yet. Run /solve first.",
        )

    net = run_data["network"]
    profile = ProfileRegistry.get(net["profile_name"])
    model_context = run_data.get("model_context") or {}

    explanations = explain_full_schedule(
        solve_result=solve_result,
        model_context=model_context,
        sections=net["sections"],
        tasks=net["maintenance_tasks"],
        train_slots=net["train_slots"],
        profile=profile,
    )

    run_store.update_run(run_id, explanations=explanations)

    items = [
        ExplainResponse(
            run_id=run_id,
            task_id=e.task_id,
            explanation=explanation_to_dict(e),
            summary=format_explanation(e),
        )
        for e in explanations
    ]

    return ExplainAllResponse(
        run_id=run_id,
        explanations=items,
    )
