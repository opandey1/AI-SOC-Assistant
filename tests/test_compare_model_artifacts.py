"""The artifact comparator behind the E3 lineage evidence, on small synthetic artifacts.

Every artifact here is built in memory from random records; no dataset is read and no
real model is loaded.
"""

import copy
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.preprocessing import StandardScaler

from scripts import compare_model_artifacts as comparator
from src.ingest import CATEGORICAL_FEATURES, MODEL_INPUT_COLUMNS
from src.model_store import ModelArtifact, save_model_artifact
from src.preprocess import _make_one_hot_encoder
from src.train import IsolationScoreCalibration

CATEGORIES = {
    "protocol_type": ["tcp", "udp", "icmp"],
    "service": ["http", "smtp", "ftp", "dns"],
    "flag": ["SF", "S0", "REJ"],
}


def toy_artifact(seed=0, rows=240):
    rng = np.random.default_rng(seed)
    raw = {
        column: (
            rng.choice(CATEGORIES[column], rows)
            if column in CATEGORIES
            else rng.exponential(10.0, rows)
        )
        for column in MODEL_INPUT_COLUMNS
    }
    frame = pd.DataFrame(raw, columns=MODEL_INPUT_COLUMNS)
    encoder = _make_one_hot_encoder().fit(frame[CATEGORICAL_FEATURES])
    encoded = pd.DataFrame(
        encoder.transform(frame[CATEGORICAL_FEATURES]),
        columns=list(encoder.get_feature_names_out(CATEGORICAL_FEATURES)),
    )
    processed = pd.concat([frame.drop(columns=CATEGORICAL_FEATURES), encoded], axis=1)
    scaler = StandardScaler().fit(processed)
    scaled = scaler.transform(processed)
    labels = rng.integers(0, 5, rows)
    forest = RandomForestClassifier(n_estimators=8, max_depth=6, random_state=seed)
    isolation = IsolationForest(n_estimators=8, random_state=seed)
    forest.fit(scaled, labels)
    isolation.fit(scaled)
    scores = isolation.decision_function(scaled)
    return ModelArtifact(
        random_forest=forest,
        isolation_forest=isolation,
        encoder=encoder,
        scaler=scaler,
        feature_names=list(processed.columns),
        isolation_calibration=IsolationScoreCalibration(
            minimum=float(scores.min()), maximum=float(scores.max())
        ),
        isolation_threshold=0.7,
        model_version="toy",
    )


def compare_with(tmp_path, change=None):
    live = toy_artifact()
    refit = copy.deepcopy(live)
    if change is not None:
        refit = change(refit) or refit
    live_path = save_model_artifact(live, tmp_path / "live.joblib")
    refit_path = save_model_artifact(refit, tmp_path / "refit.joblib")
    return comparator.compare(live_path, refit_path, probe_rows=60)


def failed(result):
    return sorted(name for name, passed in result["checks"].items() if not passed)


def test_identical_artifacts_are_equal_end_to_end(tmp_path):
    result = compare_with(tmp_path)
    assert result["equal"] is True
    assert failed(result) == []
    assert result["notes"]["probe_errors"] == []
    assert result["scope"] == list(comparator.SCOPE)


def test_a_scaler_setting_alone_changes_raw_input_scores_and_is_caught(tmp_path):
    def without_centring(artifact):
        artifact.scaler.with_mean = False

    result = compare_with(tmp_path, without_centring)
    assert result["equal"] is False
    assert failed(result) == ["end_to_end_scores", "scaler"]
    assert result["notes"]["differences"]["scaler"] == ["with_mean"]


def test_an_encoder_setting_is_caught_and_a_one_sided_failure_counts(tmp_path):
    def strict_unknowns(artifact):
        artifact.encoder.handle_unknown = "error"

    result = compare_with(tmp_path, strict_unknowns)
    assert failed(result) == ["encoder", "end_to_end_scores"]
    assert len(result["notes"]["probe_errors"]) == 1


def test_one_changed_tree_threshold_is_caught(tmp_path):
    def nudge(artifact):
        artifact.random_forest.estimators_[3].tree_.threshold[0] += 1e-9

    result = compare_with(tmp_path, nudge)
    assert "rf_trees" in failed(result)
    assert result["notes"]["differences"]["rf_trees"] == [3]


@pytest.mark.parametrize(
    "change, check",
    [
        (lambda a: replace(a, feature_names=a.feature_names[::-1]), "feature_names"),
        (
            lambda a: replace(
                a,
                isolation_calibration=IsolationScoreCalibration(
                    minimum=a.isolation_calibration.minimum - 0.1,
                    maximum=a.isolation_calibration.maximum,
                ),
            ),
            "isolation_calibration",
        ),
        (lambda a: replace(a, isolation_threshold=0.8), "isolation_threshold"),
    ],
    ids=["feature_order", "isolation_calibration", "isolation_threshold"],
)
def test_artifact_level_fields_are_compared(tmp_path, change, check):
    assert check in failed(compare_with(tmp_path, change))


def test_impurity_and_class_dtype_are_reported_but_do_not_count(tmp_path):
    def cosmetic(artifact):
        artifact.isolation_forest.estimators_[2].tree_.impurity[0] += 1e-16
        artifact.random_forest.classes_ = artifact.random_forest.classes_.astype(np.int32)

    result = compare_with(tmp_path, cosmetic)
    assert result["equal"] is True
    assert result["notes"]["if_impurity"]["trees"] == [2]
    assert result["notes"]["rf_class_dtypes"] == ["int64", "int32"]


def test_main_exits_zero_only_when_equal(tmp_path, monkeypatch, capsys):
    live = save_model_artifact(toy_artifact(), tmp_path / "live.joblib")
    other = save_model_artifact(toy_artifact(seed=1), tmp_path / "other.joblib")
    for refit, code in ((live, 0), (other, 1)):
        monkeypatch.setattr("sys.argv", ["compare", str(live), str(refit), "--probe-rows", "30"])
        assert comparator.main() == code
        assert '"equal"' in capsys.readouterr().out
