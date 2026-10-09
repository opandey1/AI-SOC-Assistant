"""Real feedback-training regressions on temporary synthetic NSL-KDD records."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import accuracy_score, f1_score

from src import train
from src.feedback import FeedbackStore
from src.ingest import LABEL_MAP, MODEL_INPUT_COLUMNS, NSL_KDD_COLUMNS, load_nsl_kdd
from src.model_store import load_model_artifact, runtime_from_artifact
from src.preprocess import preprocess_dataset, transform_connections
from src.retrain import _class_probability, build_parser, main, retrain_from_feedback, save_report
from src.runtime import ConnectionAnalysis, analyze_raw_connection
from src.train import ConnectionScore

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("normal", "dos", "probe", "r2l", "u2r")
ATTACK_LABELS = ("normal", "neptune", "satan", "guess_passwd", "buffer_overflow")


def _record(family: str, index: int = 0) -> dict:
    record = dict.fromkeys(MODEL_INPUT_COLUMNS, 0)
    record.update(protocol_type="tcp", service="http", flag="SF", src_bytes=1000, dst_bytes=2000)
    changes = {
        "normal": {},
        "dos": dict(service="private", flag="S0", duration=100, count=200, serror_rate=1),
        "probe": dict(service="ftp", flag="REJ", duration=10, count=50, diff_srv_rate=1),
        "r2l": dict(service="ftp_data", duration=20, num_failed_logins=5, logged_in=1),
        "u2r": dict(duration=30, root_shell=1, num_shells=5, hot=10),
    }
    if family != "normal":
        record.update(src_bytes=0, dst_bytes=0)
    record.update(changes[family])
    record["duration"] += index % 2
    return record


@dataclass(frozen=True)
class TrainingWorkspace:
    root: Path
    store: FeedbackStore

    @property
    def output(self) -> Path:
        return self.root / "models" / "soc_model.joblib"

    @property
    def train_path(self) -> Path:
        return self.root / "data" / "KDDTrain+.txt"

    @property
    def test_path(self) -> Path:
        return self.root / "data" / "KDDTest+.txt"

    def retrain(self, **overrides):
        options = dict(
            database_path=self.store.path,
            output_model=self.output,
            train_path=self.train_path,
            test_path=self.test_path,
        )
        return retrain_from_feedback(**(options | overrides))

    def ticket(self, family="dos", index=0, *, record=None) -> int:
        label = LABEL_MAP[family]
        analysis = ConnectionAnalysis(
            source_ip="192.0.2.10",
            raw_record=_record(family, index) if record is None else record,
            processed_record={},
            score=ConnectionScore(label, 0.9, 0.9, -0.1, 0.8, True, True, True, 0.86, "both"),
            evidence={"predicted_class": family},
            ticket="Synthetic historical incident fixture, not captured traffic.",
            model_version="synthetic-historical-model",
        )
        return self.store.log_analysis(
            analysis, event_id=f"fixture-{family}-{index}", source="retrain-test"
        )

    def review(self, ticket_id, corrected_class="normal", disposition="false_positive"):
        return self.store.record_review(
            ticket_id,
            disposition=disposition,
            corrected_class=corrected_class,
            reviewed_by="synthetic-test-analyst",
        )


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> TrainingWorkspace:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOC_ENABLE_THREAT_INTEL", "false")
    monkeypatch.setenv("SOC_LLM_PROVIDER", "template")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for filename, counts in (
        ("KDDTrain+.txt", (12, 10, 8, 6, 4)),
        ("KDDTest+.txt", (2, 2, 2, 2, 2)),
    ):
        rows = []
        for family, label, count in zip(FAMILIES, ATTACK_LABELS, counts):
            for index in range(count):
                rows.append({**_record(family, index), "label": label, "difficulty": 20})
        pd.DataFrame(rows, columns=NSL_KDD_COLUMNS).to_csv(
            data_dir / filename, header=False, index=False
        )
    return TrainingWorkspace(tmp_path, FeedbackStore(tmp_path / "state" / "feedback.db"))


@pytest.mark.parametrize("balanced", [False, True], ids=["unbalanced", "oversampled"])
def test_real_retraining_weights_latest_reviews_and_round_trips(workspace, monkeypatch, balanced):
    first = workspace.ticket(index=0)
    workspace.review(first, "u2r")
    workspace.review(first, "normal")
    second = workspace.ticket(index=1)
    workspace.review(second)
    withdrawn = workspace.ticket("probe")
    workspace.review(withdrawn)
    workspace.review(withdrawn, None, "needs_investigation")
    confirmed = workspace.ticket("r2l")
    workspace.review(confirmed, "normal", "confirmed_attack")
    workspace.ticket("u2r")
    summary_before = workspace.store.summary()
    source_bytes = (workspace.train_path.read_bytes(), workspace.test_path.read_bytes())
    rf_calls, iso_calls, scores = [], [], []
    real_rf, real_iso, real_score = (
        train.train_random_forest,
        train.train_isolation_forest,
        train.score_models,
    )

    def observe_rf(data, **kwargs):
        model = real_rf(data, **kwargs)
        rf_calls.append((data, kwargs.get("sample_weight"), model))
        return model

    def observe_iso(data, **kwargs):
        model = real_iso(data, **kwargs)
        iso_calls.append(model)
        return model

    def observe_score(*args, **kwargs):
        models = real_score(*args, **kwargs)
        scores.append(models)
        return models

    monkeypatch.setattr(train, "train_random_forest", observe_rf)
    monkeypatch.setattr(train, "train_isolation_forest", observe_iso)
    monkeypatch.setattr(train, "score_models", observe_score)
    report = workspace.retrain(feedback_weight=25.0, isolation_threshold=0.6, use_smote=balanced)
    assert len(rf_calls) == 2 and len(iso_calls) == 1 and len(scores) == 2
    data, baseline_weights, baseline = rf_calls[0]
    updated_data, weights, updated = rf_calls[1]
    assert baseline_weights is None
    assert data.balancing_applied is balanced
    assert len(data.x_train_balanced) == (60 if balanced else 40)
    examples = workspace.store.feedback_examples()
    assert [example.ticket_id for example in examples] == [first, second]
    _, feedback_x = transform_connections(pd.DataFrame([e.raw_record for e in examples]), data)
    np.testing.assert_allclose(updated_data.x_train_balanced[:-2], data.x_train_balanced)
    np.testing.assert_allclose(updated_data.x_train_balanced[-2:], feedback_x)
    np.testing.assert_array_equal(updated_data.y_train_balanced[:-2], data.y_train_balanced)
    np.testing.assert_array_equal(updated_data.y_train_balanced[-2:], [0, 0])
    np.testing.assert_array_equal(weights[:-2], np.ones(len(data.x_train_balanced)))
    np.testing.assert_array_equal(weights[-2:], [25.0, 25.0])
    assert updated_data.encoder is data.encoder and updated_data.scaler is data.scaler
    independent = preprocess_dataset(
        load_nsl_kdd(workspace.train_path, workspace.test_path), use_smote=balanced
    )
    assert data.feature_names == independent.feature_names
    np.testing.assert_allclose(data.scaler.mean_, independent.scaler.mean_)
    np.testing.assert_allclose(data.scaler.var_, independent.scaler.var_)
    for actual, expected in zip(data.encoder.categories_, independent.encoder.categories_):
        np.testing.assert_array_equal(actual, expected)
    assert scores[0].isolation_forest is scores[1].isolation_forest is iso_calls[0]
    assert scores[0].isolation_calibration is scores[1].isolation_calibration
    assert report.feedback_examples == 2
    assert report.feedback_corrected_before == 0
    assert report.feedback_corrected_after == report.feedback_predictions_changed == 2
    before_probability = baseline.predict_proba(feedback_x)[:, list(baseline.classes_).index(0)]
    after_probability = updated.predict_proba(feedback_x)[:, list(updated.classes_).index(0)]
    assert report.mean_corrected_probability_before == pytest.approx(before_probability.mean())
    assert report.mean_corrected_probability_after == pytest.approx(after_probability.mean())
    assert report.mean_corrected_probability_after > report.mean_corrected_probability_before
    # Deliberately wrong reviews help their own rows while harming the test-set labels.
    assert report.updated_accuracy < report.baseline_accuracy
    assert report.updated_macro_f1 < report.baseline_macro_f1
    for prefix, models in (("baseline", scores[0]), ("updated", scores[1])):
        assert getattr(report, f"{prefix}_accuracy") == pytest.approx(
            accuracy_score(data.y_test, models.rf_predictions)
        )
        assert getattr(report, f"{prefix}_macro_f1") == pytest.approx(
            f1_score(data.y_test, models.rf_predictions, average="macro", zero_division=0)
        )
    assert report.evaluation_rows == 10
    assert report.output_model == str(workspace.output.resolve())
    loaded = load_model_artifact(workspace.output)
    assert loaded.model_version == report.model_version
    assert loaded.metadata == dict(
        trained_at=report.trained_at,
        feedback_examples=2,
        feedback_ticket_ids=[first, second],
        feedback_weight=25.0,
        random_forest_updated=True,
        isolation_forest_updated=False,
    )
    assert loaded.feature_names == data.feature_names
    assert loaded.isolation_threshold == 0.6
    assert loaded.isolation_calibration == scores[0].isolation_calibration
    np.testing.assert_allclose(loaded.scaler.mean_, data.scaler.mean_)
    np.testing.assert_array_equal(
        loaded.random_forest.predict(feedback_x), updated.predict(feedback_x)
    )
    np.testing.assert_allclose(
        loaded.isolation_forest.decision_function(data.x_test_scaled), scores[0].iso_scores
    )
    runtime = runtime_from_artifact(loaded)
    analysis = analyze_raw_connection(_record("r2l"), runtime=runtime, provider="template")
    assert analysis.model_version == report.model_version
    assert analysis.score.fused_anomaly and analysis.ticket
    assert analysis.evidence["top_shap_drivers"]
    assert workspace.store.summary() == summary_before
    assert source_bytes == (workspace.train_path.read_bytes(), workspace.test_path.read_bytes())
    assert not workspace.output.with_name(".soc_model.joblib.tmp").exists()
    report_path = save_report(report, workspace.root / "reports" / "retrain.json")
    assert json.loads(report_path.read_text()) == asdict(report)


def test_new_corrected_class_has_zero_baseline_probability(workspace):
    frame = pd.read_csv(workspace.train_path, names=NSL_KDD_COLUMNS)
    frame[frame.label != "buffer_overflow"].to_csv(workspace.train_path, header=False, index=False)
    workspace.review(workspace.ticket(), "u2r")
    report = workspace.retrain(use_smote=False)
    assert report.mean_corrected_probability_before == 0.0
    assert report.mean_corrected_probability_after > 0.0
    assert LABEL_MAP["u2r"] in load_model_artifact(workspace.output).random_forest.classes_


def test_class_probability_uses_class_positions_and_missing_classes():
    probabilities = np.array([[0.2, 0.8], [0.3, 0.7], [0.4, 0.6]])
    np.testing.assert_allclose(
        _class_probability(probabilities, np.array([4, 0]), np.array([0, 4, 2])), [0.8, 0.3, 0.0]
    )


def test_feedback_only_category_does_not_refit_preprocessing(workspace):
    record = _record("dos")
    record["service"] = "feedback-only-service"
    workspace.review(workspace.ticket(record=record))
    workspace.retrain(use_smote=False)
    artifact = load_model_artifact(workspace.output)
    expected = preprocess_dataset(load_nsl_kdd(workspace.train_path, workspace.test_path))
    assert artifact.feature_names == expected.feature_names
    assert "feedback-only-service" not in artifact.encoder.categories_[1]
    np.testing.assert_allclose(artifact.scaler.mean_, expected.scaler.mean_)


def test_missing_explicit_dataset_does_not_fall_back_to_user_data(workspace):
    workspace.review(workspace.ticket())
    with pytest.raises(FileNotFoundError, match="Missing NSL-KDD"):
        workspace.retrain(train_path=workspace.root / "missing-train.txt")
    assert not workspace.output.exists()


@pytest.mark.parametrize("weight", [0, -1, np.nan, np.inf, -np.inf])
def test_invalid_weight_rejected_before_dataset_or_database_access(tmp_path, weight):
    database, output = tmp_path / "state.db", tmp_path / "model.joblib"
    with pytest.raises(ValueError, match="finite value greater than 0"):
        retrain_from_feedback(
            database_path=database,
            output_model=output,
            train_path=tmp_path / "missing.txt",
            test_path=tmp_path / "missing-test.txt",
            feedback_weight=weight,
        )
    assert not database.exists() and not output.exists()


def test_no_eligible_feedback_preserves_existing_output(workspace):
    workspace.output.parent.mkdir()
    workspace.output.write_bytes(b"existing-output-sentinel")
    workspace.review(workspace.ticket(), None, "confirmed_attack")
    with pytest.raises(ValueError, match="No reviewed false positives"):
        workspace.retrain()
    assert workspace.output.read_bytes() == b"existing-output-sentinel"
    assert not workspace.output.with_name(".soc_model.joblib.tmp").exists()


@pytest.mark.parametrize("kind", ["missing", "nested", "nonfinite", "nonnumeric"])
def test_invalid_feedback_preserves_existing_output(workspace, kind):
    record = _record("dos")
    if kind == "missing":
        del record["duration"]
    else:
        record["duration"] = {"nested": [], "nonfinite": "inf", "nonnumeric": "not-a-number"}[kind]
    workspace.review(workspace.ticket(record=record))
    workspace.output.parent.mkdir()
    workspace.output.write_bytes(b"existing-output-sentinel")
    message = {
        "missing": "missing required fields",
        "nested": "scalar values",
        "nonfinite": "all be finite",
        "nonnumeric": "valid numbers",
    }[kind]
    with pytest.raises(ValueError, match=message):
        workspace.retrain()
    assert workspace.output.read_bytes() == b"existing-output-sentinel"


def test_failed_updated_fit_preserves_existing_output(workspace, monkeypatch):
    workspace.review(workspace.ticket())
    workspace.output.parent.mkdir()
    workspace.output.write_bytes(b"existing-output-sentinel")
    real_fit = train.train_random_forest
    calls = []

    def fail_second_fit(data, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError("injected updated-fit failure")
        return real_fit(data, **kwargs)

    monkeypatch.setattr(train, "train_random_forest", fail_second_fit)
    with pytest.raises(RuntimeError, match="injected updated-fit failure"):
        workspace.retrain()
    assert len(calls) == 2
    assert workspace.output.read_bytes() == b"existing-output-sentinel"
    assert not workspace.output.with_name(".soc_model.joblib.tmp").exists()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "not-a-number"])
def test_cli_rejects_invalid_weight(value):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args([f"--feedback-weight={value}"])
    assert error.value.code == 2


def test_main_saves_default_weight_and_matching_stdout_report(workspace, monkeypatch, capsys):
    workspace.review(workspace.ticket())
    report_path = workspace.root / "reports" / "main.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "retrain",
            "--database",
            str(workspace.store.path),
            "--output",
            str(workspace.output),
            "--report",
            str(report_path),
            "--train",
            str(workspace.train_path),
            "--test",
            str(workspace.test_path),
        ],
    )
    main()
    stdout_report, _ = json.JSONDecoder().raw_decode(capsys.readouterr().out)
    assert stdout_report == json.loads(report_path.read_text())
    assert stdout_report["feedback_weight"] == 25.0
    assert load_model_artifact(workspace.output).model_version == stdout_report["model_version"]


def test_cli_runs_real_training_with_explicit_temporary_paths(workspace):
    workspace.review(workspace.ticket())
    report_path = workspace.root / "reports" / "cli.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.retrain",
            "--database",
            str(workspace.store.path),
            "--output",
            str(workspace.output),
            "--report",
            str(report_path),
            "--train",
            str(workspace.train_path),
            "--test",
            str(workspace.test_path),
            "--feedback-weight",
            "12.5",
            "--isolation-threshold",
            "0.6",
            "--no-smote",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    stdout_report, _ = json.JSONDecoder().raw_decode(result.stdout)
    assert stdout_report == json.loads(report_path.read_text())
    assert stdout_report["feedback_examples"] == 1
    assert stdout_report["feedback_weight"] == 12.5
    assert stdout_report["evaluation_rows"] == 10
    assert stdout_report["output_model"] == str(workspace.output.resolve())
    assert load_model_artifact(workspace.output).isolation_threshold == 0.6
