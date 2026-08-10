"""Model training and scoring for the SOC assistant."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestClassifier

from src.ingest import LABEL_MAP
from src.preprocess import PreprocessedData

DEFAULT_ISOLATION_THRESHOLD = 0.7


@dataclass(frozen=True)
class IsolationScoreCalibration:
    """Training-derived bounds used to turn detector scores into stable risk."""

    minimum: float
    maximum: float


@dataclass(frozen=True)
class ConnectionScore:
    """Canonical supervised/unsupervised verdict for one connection."""

    rf_prediction: int
    rf_confidence: float
    rf_anomaly_confidence: float
    isolation_score: float
    isolation_risk: float
    rf_anomaly: bool
    isolation_anomaly: bool
    fused_anomaly: bool
    fused_confidence: float
    alert_reason: str


@dataclass(frozen=True)
class TrainedModels:
    """Fitted detectors and their test-set scores."""

    random_forest: RandomForestClassifier
    isolation_forest: IsolationForest
    rf_probabilities: np.ndarray
    rf_predictions: np.ndarray
    rf_confidence: np.ndarray
    rf_anomaly_confidence: np.ndarray
    iso_scores: np.ndarray
    iso_predictions: np.ndarray
    iso_normalized: np.ndarray
    iso_anomaly: np.ndarray
    fused_anomaly: np.ndarray
    fused_confidence: np.ndarray
    isolation_calibration: IsolationScoreCalibration
    isolation_threshold: float


def _validate_isolation_threshold(isolation_threshold: float) -> float:
    threshold = float(isolation_threshold)
    if not np.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("isolation_threshold must be a finite value between 0 and 1.")
    return threshold


def train_random_forest(
    data: PreprocessedData,
    *,
    n_estimators: int = 200,
    max_depth: int = 25,
    min_samples_leaf: int = 2,
    random_state: int = 42,
) -> RandomForestClassifier:
    """Train the supervised multi-class attack-family classifier."""

    classifier = RandomForestClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        class_weight="balanced",
        random_state=random_state,
        n_jobs=-1,
    )
    classifier.fit(data.x_train_balanced, data.y_train_balanced)
    return classifier


def train_isolation_forest(
    data: PreprocessedData,
    *,
    n_estimators: int = 200,
    contamination: float = 0.05,
    random_state: int = 42,
) -> IsolationForest:
    """Train on normal traffic, falling back to all rows if no normal class exists."""

    normal_label = LABEL_MAP["normal"]
    normal_mask = np.asarray(data.y_train == normal_label)
    training_rows = data.x_train_scaled[normal_mask] if normal_mask.any() else data.x_train_scaled
    if len(training_rows) == 0:
        raise ValueError("Isolation Forest requires at least one training sample.")

    detector = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=random_state,
        n_jobs=-1,
    )
    detector.fit(training_rows)
    return detector


def _fit_isolation_calibration(
    isolation_forest: IsolationForest,
    data: PreprocessedData,
) -> IsolationScoreCalibration:
    """Fit score bounds once from the same training baseline as the detector."""

    normal_mask = np.asarray(data.y_train == LABEL_MAP["normal"])
    calibration_rows = (
        data.x_train_scaled[normal_mask] if normal_mask.any() else data.x_train_scaled
    )
    if len(calibration_rows) == 0:
        raise ValueError("Isolation score calibration requires at least one training sample.")

    scores = np.asarray(
        isolation_forest.decision_function(calibration_rows),
        dtype=float,
    )
    if scores.size == 0 or not np.all(np.isfinite(scores)):
        raise ValueError("Isolation Forest returned invalid training calibration scores.")
    return IsolationScoreCalibration(
        minimum=float(scores.min()),
        maximum=float(scores.max()),
    )


def _normalize_isolation_scores(
    iso_scores: np.ndarray,
    calibration: IsolationScoreCalibration,
) -> np.ndarray:
    """Map raw scores to anomaly risk using fixed training-derived bounds."""

    scores = np.asarray(iso_scores, dtype=float)
    if not np.all(np.isfinite(scores)):
        raise ValueError("Isolation Forest scores must all be finite.")
    if (
        not np.isfinite(calibration.minimum)
        or not np.isfinite(calibration.maximum)
        or calibration.maximum < calibration.minimum
    ):
        raise ValueError("Isolation score calibration bounds are invalid.")

    span = calibration.maximum - calibration.minimum
    if np.isclose(span, 0.0):
        return (scores < calibration.minimum).astype(float)

    risk = (calibration.maximum - scores) / span
    return np.clip(risk, 0.0, 1.0)


def _rf_anomaly_probabilities(
    random_forest: RandomForestClassifier,
    rf_probabilities: np.ndarray,
) -> np.ndarray:
    """Return P(any non-normal class), including single-class classifiers."""

    classes = np.asarray(random_forest.classes_)
    normal_positions = np.flatnonzero(classes == LABEL_MAP["normal"])
    if len(normal_positions) == 0:
        return np.ones(len(rf_probabilities), dtype=float)
    normal_probability = rf_probabilities[:, int(normal_positions[0])]
    return np.clip(1.0 - normal_probability, 0.0, 1.0)


def _fuse_anomaly_signals(
    rf_predictions: np.ndarray,
    rf_anomaly_confidence: np.ndarray,
    isolation_risk: np.ndarray,
    isolation_threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply the canonical alert rule and anomaly-oriented confidence formula."""

    threshold = _validate_isolation_threshold(isolation_threshold)
    rf_anomaly = np.asarray(rf_predictions) != LABEL_MAP["normal"]
    iso_anomaly = np.asarray(isolation_risk) >= threshold
    fused_anomaly = rf_anomaly | iso_anomaly
    fused_confidence = np.clip(
        0.6 * np.asarray(rf_anomaly_confidence) + 0.4 * np.asarray(isolation_risk),
        0.0,
        1.0,
    )
    return iso_anomaly.astype(int), fused_anomaly.astype(int), fused_confidence


def _alert_reason(rf_anomaly: bool, isolation_anomaly: bool) -> str:
    if rf_anomaly and isolation_anomaly:
        return "both"
    if rf_anomaly:
        return "random_forest"
    if isolation_anomaly:
        return "isolation_forest"
    return "none"


def score_models(
    random_forest: RandomForestClassifier,
    isolation_forest: IsolationForest,
    data: PreprocessedData,
    *,
    isolation_threshold: float = DEFAULT_ISOLATION_THRESHOLD,
    isolation_calibration: IsolationScoreCalibration | None = None,
) -> TrainedModels:
    """Score the test set and fuse supervised and unsupervised signals."""

    isolation_threshold = _validate_isolation_threshold(isolation_threshold)
    if isolation_calibration is None:
        isolation_calibration = _fit_isolation_calibration(isolation_forest, data)

    rf_probabilities = random_forest.predict_proba(data.x_test_scaled)
    rf_predictions = random_forest.predict(data.x_test_scaled)
    rf_confidence = rf_probabilities.max(axis=1)
    rf_anomaly_confidence = _rf_anomaly_probabilities(random_forest, rf_probabilities)

    iso_scores = isolation_forest.decision_function(data.x_test_scaled)
    iso_predictions = isolation_forest.predict(data.x_test_scaled)
    iso_normalized = _normalize_isolation_scores(iso_scores, isolation_calibration)

    iso_anomaly, fused_anomaly, fused_confidence = _fuse_anomaly_signals(
        rf_predictions,
        rf_anomaly_confidence,
        iso_normalized,
        isolation_threshold,
    )

    return TrainedModels(
        random_forest=random_forest,
        isolation_forest=isolation_forest,
        rf_probabilities=rf_probabilities,
        rf_predictions=rf_predictions,
        rf_confidence=rf_confidence,
        rf_anomaly_confidence=rf_anomaly_confidence,
        iso_scores=iso_scores,
        iso_predictions=iso_predictions,
        iso_normalized=iso_normalized,
        iso_anomaly=iso_anomaly,
        fused_anomaly=fused_anomaly,
        fused_confidence=fused_confidence,
        isolation_calibration=isolation_calibration,
        isolation_threshold=isolation_threshold,
    )


def score_connection(
    models: TrainedModels,
    row_scaled: np.ndarray,
) -> ConnectionScore:
    """Score one connection with the same calibration and rule used in batch scoring."""

    row = np.asarray(row_scaled, dtype=float).reshape(1, -1)
    rf_probabilities = models.random_forest.predict_proba(row)
    rf_prediction = int(models.random_forest.predict(row)[0])
    rf_confidence = float(rf_probabilities.max(axis=1)[0])
    rf_anomaly_confidence = float(
        _rf_anomaly_probabilities(models.random_forest, rf_probabilities)[0]
    )
    isolation_score = float(models.isolation_forest.decision_function(row)[0])
    isolation_risk = float(
        _normalize_isolation_scores(
            np.array([isolation_score]),
            models.isolation_calibration,
        )[0]
    )
    iso_anomaly, fused_anomaly, fused_confidence = _fuse_anomaly_signals(
        np.array([rf_prediction]),
        np.array([rf_anomaly_confidence]),
        np.array([isolation_risk]),
        models.isolation_threshold,
    )
    rf_anomaly = rf_prediction != LABEL_MAP["normal"]
    isolation_anomaly = bool(iso_anomaly[0])
    return ConnectionScore(
        rf_prediction=rf_prediction,
        rf_confidence=rf_confidence,
        rf_anomaly_confidence=rf_anomaly_confidence,
        isolation_score=isolation_score,
        isolation_risk=isolation_risk,
        rf_anomaly=rf_anomaly,
        isolation_anomaly=isolation_anomaly,
        fused_anomaly=bool(fused_anomaly[0]),
        fused_confidence=float(fused_confidence[0]),
        alert_reason=_alert_reason(rf_anomaly, isolation_anomaly),
    )


def train_models(
    data: PreprocessedData,
    *,
    isolation_threshold: float = DEFAULT_ISOLATION_THRESHOLD,
) -> TrainedModels:
    """Train both detectors and return their fused test-set scores."""

    random_forest = train_random_forest(data)
    isolation_forest = train_isolation_forest(data)
    return score_models(
        random_forest=random_forest,
        isolation_forest=isolation_forest,
        data=data,
        isolation_threshold=isolation_threshold,
    )
