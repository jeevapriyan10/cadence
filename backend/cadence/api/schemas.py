"""HTTP request and response Pydantic schemas for the Cadence API."""

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class GenerateNetworkRequest(BaseModel):
    """Payload for generating a synthetic railway network."""

    profile_name: str = Field(..., description="Active profile name: 'metro', 'local', or 'mainline'")
    seed: int = Field(default=42, description="Random seed for deterministic generation")
    section_count: Optional[int] = Field(default=None, ge=2, description="Target section count")
    train_count: int = Field(default=20, ge=0, description="Number of train slots to generate")
    task_count: int = Field(default=15, ge=0, description="Number of maintenance tasks to generate")

    model_config = ConfigDict(extra="ignore")


class GenerateNetworkResponse(BaseModel):
    """Response returned after synthetic network generation."""

    run_id: str
    profile_name: str
    seed: int
    section_count: int
    train_count: int
    task_count: int

    model_config = ConfigDict(extra="ignore")


class SolveRequest(BaseModel):
    """Payload to trigger CP-SAT possession scheduling for a generated run."""

    run_id: str = Field(..., description="Run identifier containing the generated network")
    time_horizon_minutes: int = Field(default=1440, ge=60, description="Planning horizon in minutes (default 24h)")
    time_limit_seconds: int = Field(default=30, ge=1, description="Solver wall-clock timeout in seconds")

    model_config = ConfigDict(extra="ignore")


class SolveResponse(BaseModel):
    """Response containing scheduled possession blocks and solver metrics."""

    run_id: str
    status: str
    scheduled_blocks: list[dict[str, Any]] = Field(default_factory=list)
    objective_value: Optional[float] = None
    wall_time_seconds: float

    model_config = ConfigDict(extra="ignore")


class RippleResponse(BaseModel):
    """Response containing downstream cascade delay propagation simulation metrics."""

    run_id: str
    total_trains_affected: int
    total_delay_minutes: float
    per_train_impacts: list[dict[str, Any]] = Field(default_factory=list)
    summary: str

    model_config = ConfigDict(extra="ignore")


class ExplainResponse(BaseModel):
    """Post-hoc scheduling explanation for an individual maintenance task."""

    run_id: str
    task_id: str
    explanation: dict[str, Any]
    summary: str

    model_config = ConfigDict(extra="ignore")


class ExplainAllResponse(BaseModel):
    """Full schedule explanation containing reasons for all scheduled tasks."""

    run_id: str
    explanations: list[ExplainResponse] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class ErrorResponse(BaseModel):
    """Standardized error response payload."""

    detail: str

    model_config = ConfigDict(extra="ignore")
