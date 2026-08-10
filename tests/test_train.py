"""Regression tests for stable anomaly scoring and signal fusion."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.train import (
    IsolationScoreCalibration,
    _normalize_isolation_scores,
    score_models,
    train_isolation_forest,
)


class _NormalRandomForest:
    classes_ = np.array([0, 1])

    def __init__(self, normal_probability: float = 0.95):
        self.normal_probability = normal_probability

    def predict_proba(self, rows):
        return np.tile(
            [self.normal_probability, 1.0 - self.normal_probability],
            (len(rows), 1),
        )

    def predict(self, rows):
        return np.zeros(len(rows), dtype=int)


class _SingleNormalClassRandomForest:
    classes_ = np.array([0])

    def predict_proba(self, rows):
        return np.ones((len(rows), 1))

    def predict(self, rows):
        return np.zeros(len(rows), dtype=int)


class _FeatureScoreIsolationForest:
    """Use column zero as the raw decision score for deterministic tests."""

    def decision_function(self, rows):
        return np.asarray(rows, dtype=float)[:, 0]

    def predict(self, rows):
        return np.where(self.decision_function(rows) < 0.0, -1, 1)


def _score_data(test_scores, *, training_scores=(0.0, 10.0)):
    return SimpleNamespace(
        x_train_scaled=np.asarray(training_scores, dtype=float).reshape(-1, 1),
        y_train=pd.Series(np.zeros(len(training_scores), dtype=int)),
        x_test_scaled=np.asarray(test_scores, dtype=float).reshape(-1, 1),
    )


def test_normalized_scores_are_bounded_and_anomaly_oriented():
    calibration = IsolationScoreCalibration(minimum=-0.3, maximum=0.5)
    iso_scores = np.array([-0.5, -0.1, 0.0, 0.2, 0.8])

    normalized = _normalize_isolation_scores(iso_scores, calibration)

    assert normalized.shape == iso_scores.shape
    assert normalized.min() >= 0.0
    assert normalized.max() <= 1.0
    assert np.argmax(normalized) == 0


def test_constant_training_calibration_is_stable_for_single_records():
    calibration = IsolationScoreCalibration(minimum=0.1, maximum=0.1)

    assert _normalize_isolation_scores(np.array([0.1]), calibration)[0] == 0.0
    assert _normalize_isolation_scores(np.array([0.0]), calibration)[0] == 1.0
    assert _normalize_isolation_scores(np.array([0.2]), calibration)[0] == 0.0


def test_score_is_independent_of_unrelated_test_batch_rows():
    rf = _NormalRandomForest()
    isolation_forest = _FeatureScoreIsolationForest()

    single = score_models(rf, isolation_forest, _score_data([2.0]))
    batch = score_models(rf, isolation_forest, _score_data([2.0, 1000.0, -1000.0]))

    assert single.iso_normalized[0] == pytest.approx(0.8)
    assert batch.iso_normalized[0] == pytest.approx(single.iso_normalized[0])
    assert single.fused_anomaly[0] == batch.fused_anomaly[0] == 1


def test_native_if_outliers_do_not_bypass_the_canonical_risk_threshold():
    """Cover the prior large mismatch where 1,767 RF-normal rows made tickets."""

    row_count = 1767
    data = _score_data(np.full(row_count, -0.1), training_scores=(-10.0, 1.0))

    models = score_models(
        _NormalRandomForest(normal_probability=0.99),
        _FeatureScoreIsolationForest(),
        data,
        isolation_threshold=0.7,
    )

    assert np.all(models.iso_predictions == -1)
    assert np.all(models.iso_normalized < models.isolation_threshold)
    assert models.fused_anomaly.sum() == 0


def test_fused_confidence_measures_anomaly_not_normal_class_confidence():
    models = score_models(
        _NormalRandomForest(normal_probability=0.99),
        _FeatureScoreIsolationForest(),
        _score_data([10.0]),
    )

    assert models.rf_confidence[0] == pytest.approx(0.99)
    assert models.rf_anomaly_confidence[0] == pytest.approx(0.01)
    assert models.fused_confidence[0] == pytest.approx(0.006)


def test_single_normal_class_has_zero_supervised_anomaly_probability():
    models = score_models(
        _SingleNormalClassRandomForest(),
        _FeatureScoreIsolationForest(),
        _score_data([10.0]),
    )

    assert models.rf_anomaly_confidence[0] == 0.0
    assert models.fused_anomaly[0] == 0


def test_isolation_forest_falls_back_when_training_has_no_normal_class():
    data = SimpleNamespace(
        x_train_scaled=np.array([[0.0], [1.0], [2.0]]),
        y_train=pd.Series([1, 1, 1]),
    )

    detector = train_isolation_forest(data, n_estimators=5, random_state=0)

    assert detector.predict(np.array([[1.0]])).shape == (1,)


@pytest.mark.parametrize("threshold", [-0.01, 1.01, np.nan, np.inf])
def test_invalid_isolation_threshold_is_rejected(threshold):
    with pytest.raises(ValueError, match="between 0 and 1"):
        score_models(
            _NormalRandomForest(),
            _FeatureScoreIsolationForest(),
            _score_data([1.0]),
            isolation_threshold=threshold,
        )
