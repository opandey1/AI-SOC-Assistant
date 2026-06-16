"""Tests for anomaly-score normalization used in fused scoring."""

import numpy as np

from src.train import _normalize_isolation_scores


def test_normalized_scores_are_bounded_in_unit_interval():
    iso_scores = np.array([-0.3, -0.1, 0.0, 0.2, 0.5])
    normalized = _normalize_isolation_scores(iso_scores)

    assert normalized.shape == iso_scores.shape
    assert normalized.min() >= 0.0
    assert normalized.max() <= 1.0


def test_more_anomalous_scores_rank_higher():
    # Isolation Forest decision_function: lower = more anomalous.
    iso_scores = np.array([-0.5, 0.0, 0.5])
    normalized = _normalize_isolation_scores(iso_scores)

    # The most anomalous sample should receive the highest normalized risk.
    assert np.argmax(normalized) == 0


def test_constant_scores_collapse_to_zero():
    iso_scores = np.array([0.1, 0.1, 0.1])
    normalized = _normalize_isolation_scores(iso_scores)

    assert np.allclose(normalized, 0.0)
