"""Pydantic v2 schemas for Cadence domain models."""

from datetime import datetime
from typing import Any, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field

from cadence.domain.models import utc_now


def generate_uuid() -> str:
    """Generate default string UUID."""
    return str(uuid.uuid4())


class TrackSectionSchema(BaseModel):
    """Schema for a TrackSection."""

    id: str = Field(default_factory=generate_uuid)
    name: str
    length_meters: Optional[float] = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class SectionAdjacencySchema(BaseModel):
    """Schema for a SectionAdjacency graph edge."""

    id: str = Field(default_factory=generate_uuid)
    section_a_id: str
    section_b_id: str

    model_config = ConfigDict(from_attributes=True)


class TrainSlotSchema(BaseModel):
    """Schema for a TrainSlot."""

    id: str = Field(default_factory=generate_uuid)
    name: str
    scheduled_start: datetime
    scheduled_end: datetime
    route: list[str] = Field(default_factory=list)
    priority: int = Field(default=1, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class MaintenanceTaskSchema(BaseModel):
    """Schema for a MaintenanceTask."""

    id: str = Field(default_factory=generate_uuid)
    name: str
    section_id: str
    duration_minutes: int = Field(..., ge=0, description="Duration in minutes (cannot be negative)")
    earliest_start: datetime
    latest_end: datetime
    priority: int = Field(default=1, ge=0)
    is_emergency: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class ScheduledBlockSchema(BaseModel):
    """Schema for a ScheduledBlock possession."""

    id: str = Field(default_factory=generate_uuid)
    task_id: str
    section_id: str
    start_time: datetime
    end_time: datetime
    method: str
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(from_attributes=True)
