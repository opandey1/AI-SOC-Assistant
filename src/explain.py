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


def _class_shap_values(
    shap_values: Any,
    class_index: int,
    *,
    feature_count: int | None = None,
) -> np.ndarray:
    """Select one model-output axis from legacy or current SHAP results."""

    if isinstance(shap_values, list):
        if class_index < 0 or class_index >= len(shap_values):
            raise ValueError(f"SHAP output does not contain class index {class_index}.")
        selected = np.asarray(shap_values[class_index])
        if selected.ndim != 2 or selected.shape[0] != 1:
            raise ValueError(f"Unsupported legacy SHAP output shape: {selected.shape}")
        result = selected[0]
        if feature_count is not None and len(result) != feature_count:
            raise ValueError("SHAP feature count does not match the model feature schema.")
        return result

    values = np.asarray(shap_values)
    if values.ndim == 3:
        if values.shape[0] == 1 and class_index < values.shape[2]:
            result = values[0, :, class_index]
        elif values.shape[1] == 1 and class_index < values.shape[0]:
            result = values[class_index, 0, :]
        else:
            raise ValueError(f"Unsupported SHAP output shape: {values.shape}")
    if values.ndim == 2:
        if values.shape[0] != 1:
            raise ValueError(f"Unsupported SHAP output shape: {values.shape}")
        result = values[0]
    elif values.ndim != 3:
        raise ValueError(f"Unsupported SHAP output shape: {values.shape}")

    if feature_count is not None and len(result) != feature_count:
        raise ValueError("SHAP feature count does not match the model feature schema.")
    return np.asarray(result)


def _expected_value_for_class(expected_value: Any, class_index: int) -> float:
    if isinstance(expected_value, list):
        if class_index < 0 or class_index >= len(expected_value):
            raise ValueError(f"Expected-value output does not contain class index {class_index}.")
        return float(expected_value[class_index])

    values = np.asarray(expected_value)
    if values.ndim == 0:
        return float(values)
    flattened = values.reshape(-1)
    if class_index >= len(flattened):
        raise ValueError(f"Expected-value output does not contain class index {class_index}.")
    return float(flattened[class_index])


def _class_index_for_prediction(random_forest: Any, prediction: Any) -> int:
    """Map a class label to its output-axis position in ``predict_proba``/SHAP."""

    if not hasattr(random_forest, "classes_"):
        raise ValueError("Random Forest is missing its fitted classes_ schema.")
    classes = np.asarray(random_forest.classes_)
    if classes.ndim != 1 or len(classes) == 0:
        raise ValueError("Random Forest classes_ must be a non-empty one-dimensional array.")
    matches = np.flatnonzero(classes == prediction)
    if len(matches) != 1:
        raise ValueError(f"Predicted label {prediction!r} is not present exactly once in classes_.")
    return int(matches[0])


def _validate_feature_schema(
    *,
    random_forest: Any,
    raw_row_df: pd.DataFrame,
    row_scaled: np.ndarray,
    feature_names: list[str],
) -> np.ndarray:
    if not isinstance(raw_row_df, pd.DataFrame) or len(raw_row_df) != 1:
        raise ValueError("raw_row_df must contain exactly one connection row.")
    if not feature_names or any(not isinstance(name, str) or not name for name in feature_names):
        raise ValueError("feature_names must contain non-empty strings.")
    if len(set(feature_names)) != len(feature_names):
        raise ValueError("feature_names contains duplicate entries.")
    if list(raw_row_df.columns) != feature_names:
        raise ValueError(
            "raw_row_df columns must exactly match feature_names in model-input order."
        )

    scaled = np.asarray(row_scaled)
    if scaled.ndim != 1 or len(scaled) != len(feature_names):
        raise ValueError("row_scaled must be one-dimensional and match feature_names.")
    model_feature_count = getattr(random_forest, "n_features_in_", len(feature_names))
    if int(model_feature_count) != len(feature_names):
        raise ValueError("Random Forest feature count does not match feature_names.")
    return scaled


def _driver_direction(shap_value: float) -> str:
    if np.isclose(shap_value, 0.0, rtol=0.0, atol=1e-12):
        return "is neutral for the predicted class"
    if shap_value > 0:
        return "supports the predicted class"
    return "opposes the predicted class"


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
    prediction: Any,
    feature_names: list[str],
    top_n: int = 8,
) -> dict[str, Any]:
    """Build a compact SHAP evidence bundle for a single connection."""

    if not isinstance(top_n, int) or top_n <= 0:
        raise ValueError("top_n must be a positive integer.")
    scaled = _validate_feature_schema(
        random_forest=random_forest,
        raw_row_df=raw_row_df,
        row_scaled=row_scaled,
        feature_names=feature_names,
    )
    class_index = _class_index_for_prediction(random_forest, prediction)
    row_scaled_2d = scaled.reshape(1, -1)
    shap_values = explainer.shap_values(row_scaled_2d)
    class_shap = _class_shap_values(
        shap_values,
        class_index,
        feature_count=len(feature_names),
    )
    top_idx = np.argsort(np.abs(class_shap))[::-1][:top_n]

    drivers: list[dict[str, Any]] = []
    for feature_idx in top_idx:
        feature = feature_names[feature_idx]
        raw_value = raw_row_df.iloc[0][feature]
        shap_value = float(class_shap[feature_idx])
        drivers.append(
            {
                "feature": feature,
                "true_value": _jsonable_value(raw_value),
                "shap_value": shap_value,
                "direction": _driver_direction(shap_value),
            }
        )

    row_proba = random_forest.predict_proba(row_scaled_2d)[0]
    if len(row_proba) != len(np.asarray(random_forest.classes_)):
        raise ValueError("predict_proba output does not match Random Forest classes_.")
    try:
        prediction_number = int(prediction)
    except (TypeError, ValueError, OverflowError):
        prediction_number = -1
    predicted_class = (
        CLASS_NAMES[prediction_number]
        if 0 <= prediction_number < len(CLASS_NAMES)
        else f"class_{prediction}"
    )

    return {
        "predicted_class": predicted_class,
        "rf_confidence": float(row_proba[class_index]),
        "top_shap_drivers": drivers,
        "base_value": _expected_value_for_class(explainer.expected_value, class_index),
    }
