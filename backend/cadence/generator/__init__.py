"""Synthetic railway network generator package for Cadence."""

from cadence.generator.network_generator import (
    generate_synthetic_network,
    persist_to_db,
)
from cadence.generator.tasks import generate_maintenance_tasks
from cadence.generator.topology import (
    generate_linear_topology,
    generate_linear_with_passing_loops_topology,
    generate_loop_topology,
)
from cadence.generator.trains import generate_train_slots

__all__ = [
    "generate_synthetic_network",
    "persist_to_db",
    "generate_loop_topology",
    "generate_linear_topology",
    "generate_linear_with_passing_loops_topology",
    "generate_train_slots",
    "generate_maintenance_tasks",
]
