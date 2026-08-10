"""Tests for canonical one-row pipeline verdicts and alert bundles."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import src.agent
import src.explain
from src.pipeline import build_parser, process_connection
from src.train import IsolationScoreCalibration


class _IdentityScaler:
    def transform(self, rows):
        return np.asarray(rows, dtype=float)


class _NormalRandomForest:
    classes_ = np.array([0, 1])

    def predict_proba(self, rows):
        return np.tile([0.98, 0.02], (len(rows), 1))

    def predict(self, rows):
        return np.zeros(len(rows), dtype=int)


class _FixedIsolationForest:
    def __init__(self, score):
        self.score = score

    def decision_function(self, rows):
        return np.full(len(rows), self.score, dtype=float)


def _models(*, score, minimum, maximum, threshold=0.7):
    return SimpleNamespace(
        random_forest=_NormalRandomForest(),
        isolation_forest=_FixedIsolationForest(score),
        isolation_calibration=IsolationScoreCalibration(minimum, maximum),
        isolation_threshold=threshold,
    )


def _data():
    return SimpleNamespace(scaler=_IdentityScaler(), feature_names=["feature_0"])


def test_raw_if_outlier_below_normalized_threshold_does_not_generate_ticket(monkeypatch):
    def fail_if_explained(**kwargs):
        raise AssertionError("a non-alerting connection must not be explained")

    monkeypatch.setattr(src.explain, "explain_connection", fail_if_explained)

    verdict = process_connection(
        pd.Series({"feature_0": 1.0}),
        data=_data(),
        models=_models(score=-0.1, minimum=-10.0, maximum=1.0),
        explainer=None,
        provider="template",
    )

    assert verdict == "Connection classified NORMAL - no ticket generated."


def test_isolation_only_alert_uses_explicit_canonical_bundle(monkeypatch):
    captured = {}

    def fake_explanation(**kwargs):
        return {
            "predicted_class": "normal",
            "rf_confidence": 0.98,
            "top_shap_drivers": [],
            "base_value": 0.0,
        }

    def fake_ticket(bundle, **kwargs):
        captured.update(bundle)
        return "ticket"

    monkeypatch.setattr(src.explain, "explain_connection", fake_explanation)
    monkeypatch.setattr(src.agent, "generate_incident_ticket", fake_ticket)

    ticket = process_connection(
        pd.Series({"feature_0": 1.0}),
        data=_data(),
        models=_models(score=1.0, minimum=0.0, maximum=10.0),
        explainer=None,
        src_ip="192.0.2.10",
        provider="template",
    )

    assert ticket == "ticket"
    assert captured["predicted_class"] == "anomaly"
    assert captured["rf_predicted_class"] == "normal"
    assert captured["alert_reason"] == "isolation_forest"
    assert captured["isolation_risk"] == pytest.approx(0.9)
    assert captured["isolation_threshold"] == pytest.approx(0.7)
    assert captured["fused_anomaly"] is True


@pytest.mark.parametrize("value", ["-0.1", "1.1", "nan", "inf"])
def test_cli_rejects_invalid_isolation_threshold(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--isolation-threshold", value])
