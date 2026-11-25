"""Operator-actionable failures shared by the retraining and registry APIs."""

from enum import Enum


class GovernanceReason(Enum):
    REGISTRY_NOT_INITIALIZED = "registry_not_initialized"
    REVIEWS_CHANGED = "reviews_changed"
    DATASET_CHANGED = "dataset_changed"
    ACTIVE_MODEL_CHANGED = "active_model_changed"
    STALE_SELECTION = "stale_selection"
    STALE_CANDIDATE_REFERENCE = "stale_candidate_reference"


class GovernanceError(ValueError):
    """Carry a stable reason code alongside the existing CLI error message."""

    def __init__(self, reason: GovernanceReason, message: str):
        super().__init__(message)
        self.reason = reason
