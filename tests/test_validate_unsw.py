"""Tests for the UNSW-NB15 schema and taxonomy adapter."""

import pandas as pd
import pytest

from src.validate_unsw import (
    COMMON_FEATURE_MAP,
    TRANSFER_CLASS_NAMES,
    evaluate_predictions,
    harmonize_unsw,
)


def _unsw_row(category: str):
    return {
        "dur": 1.0,
        "proto": "tcp",
        "service": "http",
        "state": "FIN",
        "sbytes": 120,
        "dbytes": 300,
        "is_sm_ips_ports": 0,
        "attack_cat": category,
    }


def test_harmonize_unsw_maps_supported_categories_and_reports_exclusions():
    frame = pd.DataFrame(
        [
            _unsw_row("Normal"),
            _unsw_row("DoS"),
            _unsw_row("Reconnaissance"),
            _unsw_row("Exploits"),
            _unsw_row("Shellcode"),
            _unsw_row("Generic"),
        ]
    )

    features, target, excluded = harmonize_unsw(frame)

    assert list(features.columns) == list(COMMON_FEATURE_MAP)
    assert target.tolist() == TRANSFER_CLASS_NAMES
    assert excluded == {"Generic": 1}


def test_harmonize_unsw_rejects_missing_or_invalid_common_fields():
    with pytest.raises(ValueError, match="missing required columns"):
        harmonize_unsw(pd.DataFrame([{"attack_cat": "Normal"}]))

    invalid = pd.DataFrame([_unsw_row("Normal")])
    invalid["sbytes"] = invalid["sbytes"].astype(object)
    invalid.loc[0, "sbytes"] = "not-a-number"
    with pytest.raises(ValueError, match="missing or invalid"):
        harmonize_unsw(invalid)


def test_evaluate_predictions_reports_five_class_and_binary_metrics():
    truth = pd.Series(TRANSFER_CLASS_NAMES)
    prediction = pd.Series(["normal", "dos", "probe", "normal", "u2r"])

    result = evaluate_predictions(truth, prediction.to_numpy())

    assert result.rows == 5
    assert result.accuracy == pytest.approx(0.8)
    assert result.binary_accuracy == pytest.approx(0.8)
    assert result.per_class["r2l"]["recall"] == 0.0
