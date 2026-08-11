"""Versioned, atomic persistence for fitted SOC inference artifacts."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.ingest import NslKddDataset
from src.runtime import AnalysisRuntime
from src.train import IsolationScoreCalibration

MODEL_ARTIFACT_FORMAT = 1


@dataclass(frozen=True)
class ModelArtifact:
    """Minimal fitted state required for single-connection inference."""

    random_forest: Any
    isolation_forest: Any
    encoder: Any
    scaler: Any
    feature_names: list[str]
    isolation_calibration: IsolationScoreCalibration
    isolation_threshold: float
    model_version: str
    metadata: dict[str, Any] = field(default_factory=dict)
    format_version: int = MODEL_ARTIFACT_FORMAT


def create_model_artifact(
    *,
    data: Any,
    models: Any,
    model_version: str,
    metadata: dict[str, Any] | None = None,
) -> ModelArtifact:
    return ModelArtifact(
        random_forest=models.random_forest,
        isolation_forest=models.isolation_forest,
        encoder=data.encoder,
        scaler=data.scaler,
        feature_names=list(data.feature_names),
        isolation_calibration=models.isolation_calibration,
        isolation_threshold=float(models.isolation_threshold),
        model_version=model_version,
        metadata=dict(metadata or {}),
    )


def save_model_artifact(artifact: ModelArtifact, path: str | Path) -> Path:
    """Serialize an artifact via a same-directory temporary file and atomic move."""

    import joblib

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        joblib.dump(artifact, temporary, compress=3)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def load_model_artifact(path: str | Path) -> ModelArtifact:
    """Load and validate the repository's versioned model artifact."""

    import joblib

    artifact = joblib.load(Path(path))
    if not isinstance(artifact, ModelArtifact):
        raise ValueError("Model file does not contain an AI-SOC-Assistant artifact.")
    if artifact.format_version != MODEL_ARTIFACT_FORMAT:
        raise ValueError(
            f"Unsupported model artifact format {artifact.format_version}; "
            f"expected {MODEL_ARTIFACT_FORMAT}."
        )
    return artifact


def runtime_from_artifact(
    artifact: ModelArtifact,
    *,
    dataset: NslKddDataset | None = None,
) -> AnalysisRuntime:
    """Build a live analysis runtime without retraining the saved model."""

    from src.explain import build_explainer

    return AnalysisRuntime(
        dataset=dataset,
        preprocessor=artifact,
        models=artifact,
        explainer=build_explainer(artifact.random_forest),
        model_version=artifact.model_version,
    )
