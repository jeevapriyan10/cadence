"""Metro network profile implementation."""

from typing import Any, Optional

from cadence.domain.graph import NetworkGraph
from cadence.domain.schemas import MaintenanceTaskSchema, TrainSlotSchema
from cadence.profiles.base import NetworkProfile


class MetroProfile(NetworkProfile):
    """Network profile for high-density, frequency-sensitive metro systems."""

    profile_name: str = "metro"
    default_topology: str = "loop"

    def is_safe_adjacency(
        self,
        graph: NetworkGraph,
        section_a_id: str,
        section_b_id: str,
        currently_blocked: Optional[set[str]] = None,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """Check if blocking both sections simultaneously maintains network loop connectivity.

        Unsafe if blocking both sections breaks loop connectivity in both directions simultaneously
        (i.e. partitions the graph or leaves it disconnected).
        """
        blocked = {s for s in (section_a_id, section_b_id) if s}
        if currently_blocked:
            blocked |= set(currently_blocked)

        subgraph = graph.graph.copy()
        subgraph.remove_nodes_from([node for node in blocked if node in subgraph])

        if len(subgraph) == 0:
            return False, f"Unsafe: blocking sections {sorted(blocked)} leaves no operational track sections."

        if len(graph.graph) > 2 and len(subgraph) == 1:
            return False, f"Unsafe: blocking sections {sorted(blocked)} leaves only a single isolated track section."

        simulated = NetworkGraph(subgraph)
        if not simulated.is_connected():
            return False, f"Unsafe: simultaneous blockage of sections {sorted(blocked)} breaks loop connectivity in both directions."

        return True, f"Safe: loop connectivity remains operational across active sections {sorted(subgraph.nodes())}."

    def min_headway_minutes(
        self,
        train_slot_a: TrainSlotSchema,
        train_slot_b: TrainSlotSchema,
    ) -> int:
        """Metro headway is very tight (e.g. 3-4 minutes)."""
        return 3

    def priority_weight(self, task: MaintenanceTaskSchema) -> float:
        """Emergency tasks receive a high multiplier; routine maintenance is near-flat."""
        if task.is_emergency:
            return 1000.0 + float(task.priority) * 50.0
        return 1.0 + float(task.priority) * 0.1

    def disruption_penalty(self, train_slot: TrainSlotSchema) -> float:
        """High disruption penalty due to frequency-sensitive metro riders."""
        return 100.0 * float(max(1, train_slot.priority))

    def default_topology_generator_hint(self) -> dict[str, Any]:
        """Parameters for synthetic loop generator."""
        return {
            "topology_type": "loop",
            "loop": True,
            "avg_section_count": 12,
            "bidirectional": True,
        }
