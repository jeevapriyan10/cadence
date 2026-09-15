"""Mainline network profile implementation."""

from typing import Any, Optional

from cadence.domain.graph import NetworkGraph
from cadence.domain.schemas import MaintenanceTaskSchema, TrainSlotSchema
from cadence.profiles.base import NetworkProfile


class MainlineProfile(NetworkProfile):
    """Network profile for mixed-traffic trunk lines with passing loops and sidings."""

    profile_name: str = "mainline"
    default_topology: str = "linear_with_passing_loops"

    def is_safe_adjacency(
        self,
        graph: NetworkGraph,
        section_a_id: str,
        section_b_id: str,
        currently_blocked: Optional[set[str]] = None,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """Check whether blocking sections strands a passing loop.

        Unsafe if blocking removes the only alternate path between two junction points in the graph.
        Uses graph.shortest_path to check whether an alternate path still exists after simulated removal.
        """
        blocked = {s for s in (section_a_id, section_b_id) if s}
        if currently_blocked:
            blocked |= set(currently_blocked)

        subgraph = graph.graph.copy()
        subgraph.remove_nodes_from([node for node in blocked if node in subgraph])
        simulated = NetworkGraph(subgraph)

        # Collect non-blocked neighbors of the blocked sections
        external_neighbors: set[str] = set()
        for b in blocked:
            for n in graph.get_neighbors(b):
                if n not in blocked:
                    external_neighbors.add(n)

        neighbor_list = sorted(external_neighbors)
        paths_found: list[str] = []
        for i in range(len(neighbor_list)):
            for j in range(i + 1, len(neighbor_list)):
                u = neighbor_list[i]
                v = neighbor_list[j]
                # If u and v were originally connected, check whether an alternate path remains
                if graph.shortest_path(u, v):
                    alt_path = simulated.shortest_path(u, v)
                    if not alt_path:
                        return (
                            False,
                            f"Unsafe: blocking sections {sorted(blocked)} strands passing loop; no alternate path exists between '{u}' and '{v}'.",
                        )
                    paths_found.append(f"{u} <-> {v} via {' -> '.join(alt_path)}")

        if paths_found:
            return True, f"Safe: passing loop intact; alternate bypass route exists ({', '.join(paths_found)})."
        return True, f"Safe: alternate path exists after simulated removal of {sorted(blocked)}."

    def min_headway_minutes(
        self,
        train_slot_a: TrainSlotSchema,
        train_slot_b: TrainSlotSchema,
    ) -> int:
        """Longest headway: 15 to 20 minutes (base 15, 20 if freight involved)."""
        is_freight = (
            "freight" in str(train_slot_a.attributes.get("train_type", "")).lower()
            or "freight" in str(train_slot_b.attributes.get("train_type", "")).lower()
            or "freight" in train_slot_a.name.lower()
            or "freight" in train_slot_b.name.lower()
        )
        if is_freight:
            return 20
        return 15

    def priority_weight(self, task: MaintenanceTaskSchema) -> float:
        """Passenger-tagged tasks weighted higher than freight, emergency always dominates."""
        if task.is_emergency:
            return 2000.0 + float(task.priority) * 50.0

        is_passenger = (
            task.attributes.get("traffic_type") == "passenger"
            or task.attributes.get("is_passenger", False)
            or "passenger" in str(task.attributes.get("service_type", "")).lower()
            or "passenger" in task.name.lower()
        )
        if is_passenger:
            return 30.0 * float(max(1, task.priority))
        return 10.0 * float(max(1, task.priority))

    def disruption_penalty(self, train_slot: TrainSlotSchema) -> float:
        """Lower baseline than metro/local, but scales up sharply for passenger slots specifically."""
        is_passenger = (
            train_slot.attributes.get("train_type") == "passenger"
            or train_slot.attributes.get("is_passenger", False)
            or "passenger" in str(train_slot.attributes.get("service_type", "")).lower()
            or "passenger" in train_slot.name.lower()
        )
        if is_passenger:
            return 75.0 * float(max(1, train_slot.priority))
        return 10.0 * float(max(1, train_slot.priority))

    def default_topology_generator_hint(self) -> dict[str, Any]:
        """Parameters for synthetic mainline generator."""
        return {
            "topology_type": "linear_with_passing_loops",
            "linear": True,
            "passing_loops": True,
            "avg_section_count": 25,
            "loop_frequency": 4,
        }
