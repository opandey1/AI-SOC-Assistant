"""Model training and scoring for the SOC assistant."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.preprocessing import MinMaxScaler

from src.ingest import LABEL_MAP
from src.preprocess import PreprocessedData


@dataclass(frozen=True)
class TrainedModels:
    """Fitted detectors and their test-set scores."""

    random_forest: RandomForestClassifier
    isolation_forest: IsolationForest
    rf_probabilities: np.ndarray
    rf_predictions: np.ndarray
    rf_confidence: np.ndarray
    iso_scores: np.ndarray
    iso_predictions: np.ndarray
    iso_normalized: np.ndarray
    fused_anomaly: np.ndarray
    fused_confidence: np.ndarray


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
    """Train an unsupervised anomaly detector on normal training traffic only."""

    normal_label = LABEL_MAP["normal"]
    normal_mask = data.y_train == normal_label
    if not normal_mask.any():
        raise ValueError("Isolation Forest requires at least one normal training sample.")

    detector = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=random_state,
        n_jobs=-1,
    )
    detector.fit(data.x_train_scaled[normal_mask])
    return detector


def _normalize_isolation_scores(iso_scores: np.ndarray) -> np.ndarray:
    inverted = (-iso_scores).reshape(-1, 1)
    if np.isclose(inverted.max(), inverted.min()):
        return np.zeros(len(iso_scores))
    return MinMaxScaler().fit_transform(inverted).ravel()


def score_models(
    random_forest: RandomForestClassifier,
    isolation_forest: IsolationForest,
    data: PreprocessedData,
    *,
    isolation_threshold: float = 0.7,
) -> TrainedModels:
    """Score the test set and fuse supervised and unsupervised signals."""

    rf_probabilities = random_forest.predict_proba(data.x_test_scaled)
    rf_predictions = random_forest.predict(data.x_test_scaled)
    rf_confidence = rf_probabilities.max(axis=1)

    iso_scores = isolation_forest.decision_function(data.x_test_scaled)
    iso_predictions = isolation_forest.predict(data.x_test_scaled)
    iso_normalized = _normalize_isolation_scores(iso_scores)

    fused_anomaly = (
        (rf_predictions != LABEL_MAP["normal"]) | (iso_normalized > isolation_threshold)
    ).astype(int)
    fused_confidence = 0.6 * rf_confidence + 0.4 * iso_normalized

    return TrainedModels(
        random_forest=random_forest,
        isolation_forest=isolation_forest,
        rf_probabilities=rf_probabilities,
        rf_predictions=rf_predictions,
        rf_confidence=rf_confidence,
        iso_scores=iso_scores,
        iso_predictions=iso_predictions,
        iso_normalized=iso_normalized,
        fused_anomaly=fused_anomaly,
        fused_confidence=fused_confidence,
    )


def train_models(data: PreprocessedData, *, isolation_threshold: float = 0.7) -> TrainedModels:
    """Train both detectors and return their fused test-set scores."""

    random_forest = train_random_forest(data)
    isolation_forest = train_isolation_forest(data)
    return score_models(
        random_forest=random_forest,
        isolation_forest=isolation_forest,
        data=data,
        isolation_threshold=isolation_threshold,
    )
