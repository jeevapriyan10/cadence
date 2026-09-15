"""Abstract base class defining the typed network profile interface."""

from abc import ABC, abstractmethod
from typing import Any, Optional

from cadence.domain.graph import NetworkGraph
from cadence.domain.schemas import MaintenanceTaskSchema, TrainSlotSchema


class NetworkProfile(ABC):
    """Abstract base class that every railway network profile must implement."""

    profile_name: str
    default_topology: str

    @abstractmethod
    def is_safe_adjacency(
        self,
        graph: NetworkGraph,
        section_a_id: str,
        section_b_id: str,
        currently_blocked: Optional[set[str]] = None,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """Determine whether blocking both sections simultaneously is safe.

        Returns:
            tuple[bool, str]: (is_safe, reason_string)
        """
        pass

    @abstractmethod
    def min_headway_minutes(
        self,
        train_slot_a: TrainSlotSchema,
        train_slot_b: TrainSlotSchema,
    ) -> int:
        """Minimum safe time gap between two train slots sharing a route/section."""
        pass

    @abstractmethod
    def priority_weight(self, task: MaintenanceTaskSchema) -> float:
        """Convert a task's priority and emergency status into a solver objective weight."""
        pass

    @abstractmethod
    def disruption_penalty(self, train_slot: TrainSlotSchema) -> float:
        """Cost of disrupting this train slot, used in Solve's soft objective."""
        pass

    @abstractmethod
    def default_topology_generator_hint(self) -> dict[str, Any]:
        """Dictionary of parameters used by synthetic generator to build a matching network."""
        pass
