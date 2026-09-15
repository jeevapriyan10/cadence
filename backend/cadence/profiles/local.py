"""Local railway network profile implementation."""

from typing import Any, Optional

from cadence.domain.graph import NetworkGraph
from cadence.domain.schemas import MaintenanceTaskSchema, TrainSlotSchema
from cadence.profiles.base import NetworkProfile


class LocalProfile(NetworkProfile):
    """Network profile for regional and commuter lines balancing express and local trains."""

    profile_name: str = "local"
    default_topology: str = "linear"

    def is_safe_adjacency(
        self,
        graph: NetworkGraph,
        section_a_id: str,
        section_b_id: str,
        currently_blocked: Optional[set[str]] = None,
        train_slots: Optional[list[TrainSlotSchema]] = None,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """Check whether blocking both sections isolates any section serving both express and local trains.

        Unsafe if any section with both express and local train slots scheduled through it
        becomes isolated (degree 0 in the active operational subgraph).
        """
        blocked = {s for s in (section_a_id, section_b_id) if s}
        if currently_blocked:
            blocked |= set(currently_blocked)

        mixed_sections: set[str] = set()

        # 1. Identify mixed sections from node attributes in the network graph
        for node_id, data in graph.graph.nodes(data=True):
            attrs = data.get("attributes") or {}
            if attrs.get("has_express_and_local"):
                mixed_sections.add(node_id)
            train_types = attrs.get("train_types") or attrs.get("services") or []
            if isinstance(train_types, (list, set, tuple)):
                lower_types = {str(t).lower() for t in train_types}
                if "express" in lower_types and "local" in lower_types:
                    mixed_sections.add(node_id)

        # 2. Identify mixed sections from train_slots routes if provided
        if train_slots:
            express_sections: set[str] = set()
            local_sections: set[str] = set()
            for slot in train_slots:
                slot_type = str(slot.attributes.get("train_type", "")).lower()
                slot_name = slot.name.lower()
                is_express = "express" in slot_type or "express" in slot_name or slot.attributes.get("is_express", False)
                is_local = "local" in slot_type or "local" in slot_name or slot.attributes.get("is_local", False)

                for sec in slot.route:
                    if is_express:
                        express_sections.add(sec)
                    if is_local:
                        local_sections.add(sec)

            mixed_sections |= (express_sections & local_sections)

        # Simulate graph after removing blocked sections
        subgraph = graph.graph.copy()
        subgraph.remove_nodes_from([node for node in blocked if node in subgraph])

        # Check if any mixed express/local section is now isolated
        for sec in mixed_sections:
            if sec in subgraph and subgraph.degree(sec) == 0:
                return (
                    False,
                    f"Unsafe: blocking sections {sorted(blocked)} isolates section '{sec}' which serves both express and local services.",
                )

        return True, f"Safe: no mixed express/local sections are isolated; {len(subgraph)} sections remain operational."

    def min_headway_minutes(
        self,
        train_slot_a: TrainSlotSchema,
        train_slot_b: TrainSlotSchema,
    ) -> int:
        """Medium headway between 6 and 10 minutes (base 8 mins, 10 if express meets local)."""
        is_a_express = "express" in str(train_slot_a.attributes.get("train_type", "")).lower() or "express" in train_slot_a.name.lower()
        is_b_express = "express" in str(train_slot_b.attributes.get("train_type", "")).lower() or "express" in train_slot_b.name.lower()
        if is_a_express != is_b_express:
            return 10
        return 8

    def priority_weight(self, task: MaintenanceTaskSchema) -> float:
        """Emergency tasks dominate; tasks conflicting with express trains are weighted higher than pure-local."""
        if task.is_emergency:
            return 1000.0 + float(task.priority) * 50.0

        affects_express = (
            task.attributes.get("affects_express", False)
            or task.attributes.get("conflict_type") == "express"
            or "express" in str(task.attributes.get("train_types", [])).lower()
            or "express" in task.name.lower()
        )
        if affects_express:
            return 25.0 * float(max(1, task.priority))
        return 10.0 * float(max(1, task.priority))

    def disruption_penalty(self, train_slot: TrainSlotSchema) -> float:
        """Medium disruption penalty weighted by total train slots passing through the section."""
        base_penalty = 25.0 * float(max(1, train_slot.priority))
        slot_density = float(
            train_slot.attributes.get("slots_on_section")
            or train_slot.attributes.get("combined_slot_count")
            or 1
        )
        return base_penalty * max(1.0, slot_density)

    def default_topology_generator_hint(self) -> dict[str, Any]:
        """Parameters for synthetic linear generator."""
        return {
            "topology_type": "linear",
            "linear": True,
            "avg_section_count": 15,
            "has_express_stops": True,
        }
