"""Tests for versioned model artifact persistence."""

from dataclasses import replace
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


@pytest.mark.parametrize("stage", ["serialize", "replace"])
def test_failed_write_preserves_previous_artifact_and_removes_temporary_file(
    tmp_path, monkeypatch, stage
):
    output = tmp_path / "models" / "soc_model.joblib"
    save_model_artifact(_artifact(), output)
    previous_bytes = output.read_bytes()

    def fail_dump(artifact, path, **kwargs):
        Path(path).write_bytes(b"injected-partial-serialization")
        raise OSError("injected serialization failure")

    def fail_replace(source, destination):
        assert Path(source).exists()
        assert Path(destination) == output
        raise OSError("injected replacement failure")

    if stage == "serialize":
        monkeypatch.setattr(joblib, "dump", fail_dump)
    else:
        monkeypatch.setattr("src.model_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        save_model_artifact(replace(_artifact(), model_version="replacement"), output)
    assert output.read_bytes() == previous_bytes
    assert load_model_artifact(output).model_version == "feedback-test"
    assert not output.with_name(".soc_model.joblib.tmp").exists()


def test_model_store_rejects_an_unsupported_format(tmp_path):
    output = tmp_path / "unsupported.joblib"
    joblib.dump(replace(_artifact(), format_version=999), output)
    with pytest.raises(ValueError, match="Unsupported model artifact format 999"):
        load_model_artifact(output)
