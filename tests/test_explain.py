"""Tests for SHAP evidence extraction and cross-version output handling."""

import numpy as np
import pandas as pd

from src.explain import (
    _class_shap_values,
    _expected_value_for_class,
    _jsonable_value,
    build_explainer,
    explain_connection,
)


def test_class_shap_values_handles_legacy_list_output():
    shap_values = [np.array([[1.0, 2.0, 3.0]]), np.array([[4.0, 5.0, 6.0]])]
    np.testing.assert_array_equal(_class_shap_values(shap_values, 1), [4.0, 5.0, 6.0])


def test_class_shap_values_handles_3d_array_output():
    # Newer SHAP returns (n_samples, n_features, n_classes).
    values = np.arange(1 * 3 * 2).reshape(1, 3, 2)
    np.testing.assert_array_equal(_class_shap_values(values, 1), values[0, :, 1])


def test_jsonable_value_normalizes_numpy_and_nan():
    assert _jsonable_value(np.int64(5)) == 5
    assert isinstance(_jsonable_value(np.float64(1.5)), float)
    assert _jsonable_value(np.nan) is None
    assert _jsonable_value("tcp") == "tcp"


def test_expected_value_for_class_supports_list_and_scalar():
    assert _expected_value_for_class([0.1, 0.2, 0.3], 2) == 0.3
    assert _expected_value_for_class(np.array(0.4), 0) == 0.4


def test_explain_connection_returns_well_formed_bundle():
    from sklearn.ensemble import RandomForestClassifier

    rng = np.random.default_rng(0)
    x = rng.random((60, 4))
    y = (x[:, 0] + x[:, 1] > 1.0).astype(int)
    features = [f"f{i}" for i in range(4)]

    rf = RandomForestClassifier(n_estimators=15, random_state=0).fit(x, y)
    explainer = build_explainer(rf)

    row = x[:1]
    prediction = int(rf.predict(row)[0])
    bundle = explain_connection(
        explainer=explainer,
        random_forest=rf,
        raw_row_df=pd.DataFrame(row, columns=features),
        row_scaled=row[0],
        prediction=prediction,
        feature_names=features,
        top_n=3,
    )

    assert set(bundle) == {"predicted_class", "rf_confidence", "top_shap_drivers", "base_value"}
    assert len(bundle["top_shap_drivers"]) == 3
    assert 0.0 <= bundle["rf_confidence"] <= 1.0
    for driver in bundle["top_shap_drivers"]:
        assert set(driver) == {"feature", "true_value", "shap_value", "direction"}
        assert driver["direction"] in {"increases risk", "decreases risk"}
