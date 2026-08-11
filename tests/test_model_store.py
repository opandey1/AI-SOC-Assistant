"""Tests for versioned model artifact persistence."""

from pathlib import Path

import joblib
import pytest

from src.model_store import ModelArtifact, load_model_artifact, save_model_artifact
from src.train import IsolationScoreCalibration


def _artifact():
    return ModelArtifact(
        random_forest={"kind": "rf"},
        isolation_forest={"kind": "if"},
        encoder={"kind": "ohe"},
        scaler={"kind": "scaler"},
        feature_names=["duration"],
        isolation_calibration=IsolationScoreCalibration(-0.2, 0.3),
        isolation_threshold=0.7,
        model_version="feedback-test",
    )


def test_model_artifact_round_trip_is_atomic(tmp_path: Path):
    output = tmp_path / "models" / "soc_model.joblib"

    saved = save_model_artifact(_artifact(), output)
    loaded = load_model_artifact(saved)

    assert loaded.model_version == "feedback-test"
    assert loaded.feature_names == ["duration"]
    assert not (output.parent / ".soc_model.joblib.tmp").exists()


def test_model_store_rejects_an_unversioned_payload(tmp_path: Path):
    output = tmp_path / "bad.joblib"
    joblib.dump({"random_forest": "not-an-artifact"}, output)

    with pytest.raises(ValueError, match="does not contain"):
        load_model_artifact(output)
