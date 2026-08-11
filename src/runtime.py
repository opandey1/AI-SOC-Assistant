"""Reusable inference runtime for CLI, streaming, and dashboard workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.ingest import INVERSE_LABEL_MAP, NslKddDataset
from src.train import ConnectionScore, score_connection

NORMAL_VERDICT = "Connection classified NORMAL - no ticket generated."


@dataclass(frozen=True)
class AnalysisRuntime:
    """Fitted preprocessing, detection, and explanation objects."""

    dataset: NslKddDataset | None
    preprocessor: Any
    models: Any
    explainer: Any
    model_version: str = "baseline-nsl-kdd"


@dataclass(frozen=True)
class ConnectionAnalysis:
    """Structured output for one connection event."""

    source_ip: str
    raw_record: dict[str, Any]
    processed_record: dict[str, float]
    score: ConnectionScore
    evidence: dict[str, Any]
    ticket: str | None
    model_version: str

    @property
    def predicted_class(self) -> str:
        return str(self.evidence.get("predicted_class", "unknown"))

    @property
    def verdict(self) -> str:
        return "alert" if self.score.fused_anomaly else "normal"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    missing = pd.isna(value)
    if isinstance(missing, (bool, np.bool_)) and missing:
        return None
    return str(value)


def jsonable_record(record: Any) -> dict[str, Any]:
    """Return a JSON-safe dictionary from a mapping or pandas row."""

    values = record.to_dict() if isinstance(record, pd.Series) else dict(record)
    return {str(key): _jsonable(value) for key, value in values.items()}


def build_runtime(
    train_path: str | Path | None = None,
    test_path: str | Path | None = None,
    *,
    search_roots: list[str | Path] | None = None,
    use_smote: bool = True,
    isolation_threshold: float = 0.7,
) -> AnalysisRuntime:
    """Load NSL-KDD, train both detectors once, and build the SHAP explainer."""

    from src.explain import build_explainer
    from src.ingest import load_nsl_kdd
    from src.preprocess import preprocess_dataset
    from src.train import train_models

    roots = search_roots or [Path(__file__).resolve().parents[1], Path.cwd()]
    dataset = load_nsl_kdd(train_path, test_path, search_roots=roots)
    data = preprocess_dataset(dataset, use_smote=use_smote)
    models = train_models(data, isolation_threshold=isolation_threshold)
    return AnalysisRuntime(
        dataset=dataset,
        preprocessor=data,
        models=models,
        explainer=build_explainer(models.random_forest),
    )


def _build_evidence(
    *,
    processed_row: pd.Series,
    row_scaled: np.ndarray,
    runtime: AnalysisRuntime,
    score: ConnectionScore,
    source_ip: str,
    include_shap: bool,
) -> dict[str, Any]:
    rf_predicted_class = INVERSE_LABEL_MAP.get(
        score.rf_prediction,
        f"class_{score.rf_prediction}",
    )
    if include_shap:
        from src.explain import explain_connection

        row_df = pd.DataFrame([processed_row], columns=runtime.preprocessor.feature_names)
        evidence = explain_connection(
            explainer=runtime.explainer,
            random_forest=runtime.models.random_forest,
            raw_row_df=row_df,
            row_scaled=row_scaled,
            prediction=score.rf_prediction,
            feature_names=runtime.preprocessor.feature_names,
        )
    else:
        evidence = {
            "predicted_class": rf_predicted_class,
            "rf_confidence": score.rf_confidence,
            "top_shap_drivers": [],
            "base_value": None,
        }

    evidence["rf_predicted_class"] = rf_predicted_class
    if score.alert_reason == "isolation_forest":
        evidence["predicted_class"] = "anomaly"
    evidence.update(
        {
            "rf_confidence": score.rf_confidence,
            "rf_anomaly_confidence": score.rf_anomaly_confidence,
            "isolation_forest_score": score.isolation_score,
            "isolation_risk": score.isolation_risk,
            "isolation_threshold": runtime.models.isolation_threshold,
            "rf_anomaly": score.rf_anomaly,
            "isolation_anomaly": score.isolation_anomaly,
            "fused_anomaly": score.fused_anomaly,
            "fused_confidence": score.fused_confidence,
            "alert_reason": score.alert_reason,
            "source_ip": source_ip,
            "model_version": runtime.model_version,
        }
    )
    return evidence


def analyze_processed_connection(
    processed_row: pd.Series,
    *,
    runtime: AnalysisRuntime,
    raw_record: Any | None = None,
    source_ip: str | None = None,
    provider: str | None = None,
    explain_normal: bool = True,
) -> ConnectionAnalysis:
    """Analyze a processed feature row and optionally generate an incident ticket."""

    from src.agent import generate_incident_ticket

    source = source_ip or "unknown"
    row_df = pd.DataFrame([processed_row], columns=runtime.preprocessor.feature_names)
    row_scaled = np.asarray(runtime.preprocessor.scaler.transform(row_df)[0], dtype=float)
    score = score_connection(runtime.models, row_scaled)
    evidence = _build_evidence(
        processed_row=processed_row,
        row_scaled=row_scaled,
        runtime=runtime,
        score=score,
        source_ip=source,
        include_shap=score.fused_anomaly or explain_normal,
    )
    ticket = (
        generate_incident_ticket(evidence, src_ip=source_ip, provider=provider)
        if score.fused_anomaly
        else None
    )
    return ConnectionAnalysis(
        source_ip=source,
        raw_record=jsonable_record({} if raw_record is None else raw_record),
        processed_record={str(key): float(value) for key, value in processed_row.to_dict().items()},
        score=score,
        evidence=evidence,
        ticket=ticket,
        model_version=runtime.model_version,
    )


def analyze_raw_connection(
    raw_record: Any,
    *,
    runtime: AnalysisRuntime,
    source_ip: str | None = None,
    provider: str | None = None,
    explain_normal: bool = True,
) -> ConnectionAnalysis:
    """Validate, transform, score, and explain one raw NSL-KDD-shaped record."""

    from src.preprocess import transform_connection

    processed_row, _ = transform_connection(raw_record, runtime.preprocessor)
    return analyze_processed_connection(
        processed_row,
        runtime=runtime,
        raw_record=raw_record,
        source_ip=source_ip,
        provider=provider,
        explain_normal=explain_normal,
    )
