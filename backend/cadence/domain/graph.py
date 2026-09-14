"""Railway track network graph representation wrapping NetworkX."""

from typing import Optional
import networkx as nx

from cadence.domain.models import SectionAdjacency, TrackSection


class NetworkGraph:
    """Wraps a NetworkX Graph to represent the railway track network."""

    def __init__(self, graph: Optional[nx.Graph] = None) -> None:
        self.graph: nx.Graph = graph if graph is not None else nx.Graph()

    @classmethod
    def build_from_sections(
        cls,
        sections: list[TrackSection],
        adjacencies: list[SectionAdjacency],
    ) -> "NetworkGraph":
        """Construct a NetworkGraph from TrackSection nodes and SectionAdjacency edges."""
        graph = nx.Graph()
        for section in sections:
            graph.add_node(
                section.id,
                name=section.name,
                length_meters=section.length_meters,
                attributes=section.attributes,
            )
        for adj in adjacencies:
            graph.add_edge(adj.section_a_id, adj.section_b_id)
        return cls(graph=graph)

    def are_adjacent(self, section_a_id: str, section_b_id: str) -> bool:
        """Check whether two track sections share an edge."""
        return self.graph.has_edge(section_a_id, section_b_id)

    def get_neighbors(self, section_id: str) -> list[str]:
        """Return list of adjacent section IDs for a given section."""
        if section_id not in self.graph:
            return []
        return list(self.graph.neighbors(section_id))

    def is_connected(self) -> bool:
        """Check if the railway network graph is a single connected component."""
        if len(self.graph) == 0:
            return False
        return nx.is_connected(self.graph)

    def shortest_path(self, section_a_id: str, section_b_id: str) -> list[str]:
        """Compute the shortest path (list of section IDs) between two sections."""
        try:
            return list(nx.shortest_path(self.graph, source=section_a_id, target=section_b_id))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
