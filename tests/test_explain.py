"""Tests for SHAP evidence extraction and cross-version output handling."""

import numpy as np
import pandas as pd
import pytest

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
        assert driver["direction"] in {
            "supports the predicted class",
            "opposes the predicted class",
            "is neutral for the predicted class",
        }


def test_explain_connection_maps_non_contiguous_class_label_to_output_axis():
    from sklearn.ensemble import RandomForestClassifier

    rng = np.random.default_rng(3)
    x = rng.random((80, 3))
    y = np.where(x[:, 0] > 0.5, 2, 0)
    features = ["f0", "f1", "f2"]
    rf = RandomForestClassifier(n_estimators=20, random_state=3).fit(x, y)
    explainer = build_explainer(rf)
    row = x[y == 2][:1]

    bundle = explain_connection(
        explainer=explainer,
        random_forest=rf,
        raw_row_df=pd.DataFrame(row, columns=features),
        row_scaled=row[0],
        prediction=2,
        feature_names=features,
        top_n=2,
    )

    assert list(rf.classes_) == [0, 2]
    assert bundle["predicted_class"] == "probe"
    assert bundle["rf_confidence"] == pytest.approx(rf.predict_proba(row)[0, 1])


def test_explain_connection_fails_closed_on_feature_schema_mismatch():
    class FakeForest:
        classes_ = np.array([0, 1])
        n_features_in_ = 2

    class FakeExplainer:
        expected_value = [0.5, 0.5]

        def shap_values(self, row):  # pragma: no cover - validation runs first
            raise AssertionError("SHAP must not run against a mismatched feature schema")

    with pytest.raises(ValueError, match="exactly match feature_names"):
        explain_connection(
            explainer=FakeExplainer(),
            random_forest=FakeForest(),
            raw_row_df=pd.DataFrame([[1.0]], columns=["f0"]),
            row_scaled=np.array([1.0, 2.0]),
            prediction=1,
            feature_names=["f0", "f1"],
        )


def test_driver_directions_describe_predicted_class_and_include_neutral():
    class FakeForest:
        classes_ = np.array([0, 2])
        n_features_in_ = 3

        def predict_proba(self, row):
            return np.array([[0.1, 0.9]])

    class FakeExplainer:
        expected_value = [0.4, 0.6]

        def shap_values(self, row):
            return [
                np.array([[-0.5, 0.2, 0.0]]),
                np.array([[0.5, -0.2, 0.0]]),
            ]

    features = ["supports", "opposes", "neutral"]
    bundle = explain_connection(
        explainer=FakeExplainer(),
        random_forest=FakeForest(),
        raw_row_df=pd.DataFrame([[10, 20, 30]], columns=features),
        row_scaled=np.array([0.1, 0.2, 0.3]),
        prediction=2,
        feature_names=features,
        top_n=3,
    )
    directions = {driver["feature"]: driver["direction"] for driver in bundle["top_shap_drivers"]}

    assert directions == {
        "supports": "supports the predicted class",
        "opposes": "opposes the predicted class",
        "neutral": "is neutral for the predicted class",
    }


def test_prediction_missing_from_model_classes_is_rejected():
    class FakeForest:
        classes_ = np.array([0, 2])
        n_features_in_ = 1

    class FakeExplainer:
        expected_value = [0.5, 0.5]

        def shap_values(self, row):  # pragma: no cover - class validation runs first
            raise AssertionError("SHAP should not run for an unknown predicted label")

    with pytest.raises(ValueError, match="not present exactly once"):
        explain_connection(
            explainer=FakeExplainer(),
            random_forest=FakeForest(),
            raw_row_df=pd.DataFrame([[1.0]], columns=["f0"]),
            row_scaled=np.array([1.0]),
            prediction=1,
            feature_names=["f0"],
        )
