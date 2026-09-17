"""In-memory execution run store for Cadence API sessions."""

from typing import Any, Optional
import uuid


# =============================================================================
# IN-MEMORY RUN STORE (NON-PERSISTENT BY DESIGN)
# -----------------------------------------------------------------------------
# This in-memory dictionary-backed store is intentionally temporary and
# non-persistent. It acts as a lightweight session cache for development and
# fast end-to-end API orchestration across Track, Solve, Ripple, and Reason.
# In the upcoming Echo module, this will be completely replaced by real
# database-backed persistence (SQLAlchemy / Alembic models) storing historical
# runs, schedule versions, and audit trails.
# =============================================================================


class RunStore:
    """Simple in-memory store for railway scheduling runs, keyed by UUID4 run_id."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}

    def create_run(
        self,
        network_data: Optional[dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> str:
        """Initialize a new run record with optional network data.

        Args:
            network_data: Dictionary containing sections, adjacencies, train_slots,
                maintenance_tasks, profile_name, seed.
            run_id: Optional preset UUID; if None, a new UUID4 string is generated.

        Returns:
            str: Unique run_id string.
        """
        allocated_id = run_id or str(uuid.uuid4())
        self._runs[allocated_id] = {
            "run_id": allocated_id,
            "network": network_data,
            "solve_result": None,
            "model_context": None,
            "ripple_report": None,
            "explanations": None,
        }
        return allocated_id

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        """Retrieve the run record for a given run_id.

        Args:
            run_id: Unique run identifier.

        Returns:
            Optional[dict[str, Any]]: Run dictionary if found, None otherwise.
        """
        return self._runs.get(run_id)

    def update_run(self, run_id: str, **kwargs: Any) -> None:
        """Update fields of an existing run record.

        Args:
            run_id: Unique run identifier.
            **kwargs: Key-value attributes to store (e.g. solve_result, ripple_report).
        """
        if run_id in self._runs:
            self._runs[run_id].update(kwargs)

    def has_run(self, run_id: str) -> bool:
        """Check if a run_id exists in the store."""
        return run_id in self._runs

    def clear(self) -> None:
        """Clear all stored runs (used primarily in test fixtures)."""
        self._runs.clear()


# Module-level singleton instance for API routes
run_store = RunStore()
