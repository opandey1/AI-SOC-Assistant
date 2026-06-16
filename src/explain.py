"""SHAP explanation utilities for model decisions."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import shap

from src.ingest import CLASS_NAMES


def build_explainer(random_forest: Any) -> shap.TreeExplainer:
    """Create a SHAP tree explainer for the trained Random Forest."""

    return shap.TreeExplainer(random_forest)


def _class_shap_values(shap_values: Any, prediction: int) -> np.ndarray:
    if isinstance(shap_values, list):
        return np.asarray(shap_values[prediction][0])

    values = np.asarray(shap_values)
    if values.ndim == 3:
        if values.shape[0] == 1:
            return values[0, :, prediction]
        if values.shape[1] == 1:
            return values[prediction, 0, :]
        return values[0, :, prediction]
    if values.ndim == 2:
        return values[0]

    raise ValueError(f"Unsupported SHAP output shape: {values.shape}")


def _expected_value_for_class(expected_value: Any, prediction: int) -> float:
    if isinstance(expected_value, list):
        return float(expected_value[prediction])

    values = np.asarray(expected_value)
    if values.ndim == 0:
        return float(values)
    if len(values) > prediction:
        return float(values[prediction])
    return float(values[0])


def _jsonable_value(value: Any) -> int | float | str | None:
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    return str(value)


def explain_connection(
    *,
    explainer: shap.TreeExplainer,
    random_forest: Any,
    raw_row_df: pd.DataFrame,
    row_scaled: np.ndarray,
    prediction: int,
    feature_names: list[str],
    top_n: int = 8,
) -> dict[str, Any]:
    """Build a compact SHAP evidence bundle for a single connection."""

    row_scaled_2d = row_scaled.reshape(1, -1)
    shap_values = explainer.shap_values(row_scaled_2d)
    class_shap = _class_shap_values(shap_values, prediction)
    top_idx = np.argsort(np.abs(class_shap))[::-1][:top_n]

    drivers: list[dict[str, Any]] = []
    for feature_idx in top_idx:
        feature = feature_names[feature_idx]
        raw_value = (
            raw_row_df.iloc[0][feature]
            if feature in raw_row_df.columns
            else raw_row_df.iloc[0, feature_idx]
        )
        shap_value = float(class_shap[feature_idx])
        drivers.append(
            {
                "feature": feature,
                "true_value": _jsonable_value(raw_value),
                "shap_value": shap_value,
                "direction": "increases risk" if shap_value > 0 else "decreases risk",
            }
        )

    row_proba = random_forest.predict_proba(row_scaled_2d)[0]
    predicted_class = (
        CLASS_NAMES[prediction] if prediction < len(CLASS_NAMES) else f"class_{prediction}"
    )

    return {
        "predicted_class": predicted_class,
        "rf_confidence": float(row_proba.max()),
        "top_shap_drivers": drivers,
        "base_value": _expected_value_for_class(explainer.expected_value, prediction),
    }
