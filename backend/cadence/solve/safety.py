"""Safety-adjacency precomputation for railway maintenance scheduling."""

from typing import Any, Union

from cadence.domain.graph import NetworkGraph
from cadence.domain.models import TrackSection
from cadence.domain.schemas import TrackSectionSchema
from cadence.profiles.base import NetworkProfile


def precompute_unsafe_adjacency_pairs(
    graph: NetworkGraph,
    profile: NetworkProfile,
    sections: list[Union[TrackSectionSchema, TrackSection]],
    **kwargs: Any,
) -> list[tuple[str, str, str]]:
    """Precompute all pairs of adjacent track sections that cannot be safely blocked simultaneously.

    Enumerates candidate adjacent pairs via graph.get_neighbors to avoid O(N^2) brute-forcing,
    calls the active NetworkProfile's is_safe_adjacency() method, and returns the list of
    flagged unsafe pairs along with their explanatory reason strings.

    Args:
        graph: NetworkGraph representation of the track network.
        profile: Active NetworkProfile defining safety adjacency rules.
        sections: Track sections in the network (TrackSectionSchema or TrackSection models).
        **kwargs: Additional contextual keyword arguments passed to is_safe_adjacency
            (e.g., train_slots for LocalProfile).

    Returns:
        list[tuple[str, str, str]]: List of (section_a_id, section_b_id, reason) tuples
            for every adjacent pair deemed unsafe to block simultaneously.
    """
    if graph is None or not sections:
        return []

    seen_pairs: set[tuple[str, str]] = set()
    unsafe_pairs: list[tuple[str, str, str]] = []

    for section in sections:
        sec_id = section.id if hasattr(section, "id") else section["id"]
        neighbors = graph.get_neighbors(sec_id)

        for neighbor_id in neighbors:
            if sec_id == neighbor_id:
                continue

            pair_key = tuple(sorted((sec_id, neighbor_id)))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            sec_a, sec_b = pair_key
            is_safe, reason = profile.is_safe_adjacency(
                graph=graph,
                section_a_id=sec_a,
                section_b_id=sec_b,
                currently_blocked=set(),
                **kwargs,
            )

            if not is_safe:
                unsafe_pairs.append((sec_a, sec_b, reason))

    return unsafe_pairs
