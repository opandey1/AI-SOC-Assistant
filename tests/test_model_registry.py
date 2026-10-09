"""Candidate selection safety with isolated bundles and real SQLite transactions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import sqlite3
import sys
from uuid import uuid4

import pytest

from src.model_registry import ModelRegistry, acceptance_reasons
from src.model_store import ModelArtifact, save_model_artifact
from src.retrain import RetrainingReport, file_digest
from src.train import IsolationScoreCalibration


@pytest.fixture
def report():
    recall = dict.fromkeys(("normal", "dos", "probe", "r2l", "u2r"), 1.0)
    return asdict(
        RetrainingReport(
            model_version="test-candidate",
            trained_at="2026-10-09T00:00:00Z",
            feedback_examples=5,
            feedback_weight=25.0,
            feedback_predictions_changed=0,
            feedback_corrected_before=5,
            feedback_corrected_after=5,
            mean_corrected_probability_before=1.0,
            mean_corrected_probability_after=1.0,
            evaluation_rows=10,
            baseline_accuracy=1.0,
            updated_accuracy=1.0,
            baseline_macro_f1=1.0,
            updated_macro_f1=1.0,
            output_model="test-only",
            baseline_per_class_recall=recall,
            updated_per_class_recall=recall,
            evaluation_support=dict.fromkeys(recall, 2),
        )
    )


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return ModelRegistry(tmp_path / "registry")


def register(registry, report, *, parent=0, reference=None):
    """Fixture bundles are not training/evaluation evidence; separate real-fit tests are."""
    candidate_id = uuid4().hex
    directory = registry.root / candidate_id
    directory.mkdir(parents=True)
    artifact = ModelArtifact(
        {},
        {},
        {},
        {},
        ["duration"],
        IsolationScoreCalibration(-0.2, 0.3),
        0.7,
        report["model_version"],
    )
    save_model_artifact(artifact, directory / "model.joblib")
    (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
    reasons = acceptance_reasons(report)
    manifest = dict(
        id=candidate_id,
        kind="feedback",
        created_at=report["trained_at"],
        model_version=report["model_version"],
        parent_generation=parent,
        reference_sha256=reference,
        active_reference_metrics=None,
        accepted=not reasons,
        reasons=reasons,
        sha256={name: file_digest(directory / name) for name in ("model.joblib", "report.json")},
    )
    with registry._connect(initialize=True) as connection:
        registry._register(manifest, connection)
    return candidate_id


def test_empty_registry_reads_do_not_create_storage(registry):
    assert registry.state() == dict(active=None, previous=None, generation=0)
    assert registry.active_path() is None
    assert registry.candidates() == registry.history() == []
    assert not registry.root.exists()


@pytest.mark.parametrize("metric", ["accuracy", "macro_f1"])
def test_aggregate_regression_rejected(report, metric):
    report[f"updated_{metric}"] = 0.99
    assert f"regressed_{metric}" in acceptance_reasons(report)


@pytest.mark.parametrize("family", ["normal", "dos", "probe", "r2l", "u2r"])
def test_each_class_recall_regression_rejected(report, family):
    report["updated_per_class_recall"][family] = 0.5
    assert f"regressed_recall_{family}" in acceptance_reasons(report)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True, None])
def test_nonfinite_or_invalid_metrics_fail_closed(report, value):
    report["updated_accuracy"] = value
    assert "invalid_accuracy" in acceptance_reasons(report)
    report["updated_per_class_recall"]["u2r"] = value
    assert "invalid_recall_u2r" in acceptance_reasons(report)


def test_missing_class_and_unknown_population_rejected(report):
    report["evaluation_support"]["u2r"] = 0
    reasons = acceptance_reasons(report)
    assert "missing_evaluation_class_u2r" in reasons
    assert "invalid_evaluation_population" in reasons


def test_reference_comparison_is_not_just_published_baseline(report):
    report["baseline_accuracy"] = report["baseline_macro_f1"] = 0.8
    report["updated_accuracy"] = report["updated_macro_f1"] = 0.9
    assert not acceptance_reasons(report)
    reference = dict(accuracy=1.0, macro_f1=1.0, recall=report["baseline_per_class_recall"])
    assert {"regressed_accuracy", "regressed_macro_f1"} <= set(
        acceptance_reasons(report, reference)
    )


def test_promotion_and_rollback_are_explicit_and_audited(registry, report):
    candidate = register(registry, report)
    assert registry.active_path() is None
    assert registry.report(candidate) == report
    assert registry.promote(candidate, expected_generation=0, actor="alice") == dict(
        active=candidate, previous=None, generation=1
    )
    path = registry.active_path()
    original = path.read_bytes()
    assert registry.rollback(expected_generation=1, actor="bob") == dict(
        active=None, previous=candidate, generation=2
    )
    assert registry.active_path() is None
    assert path.read_bytes() == original
    registry.rollback(expected_generation=2, actor="alice")
    assert registry.active_path() == path
    assert [(item["action"], item["actor"]) for item in registry.history()] == [
        ("promote", "alice"),
        ("rollback", "bob"),
        ("rollback", "alice"),
    ]


def test_legacy_first_promotion_has_an_immutable_rollback_snapshot(registry, report, tmp_path):
    legacy = tmp_path / "legacy.joblib"
    legacy.write_bytes(b"trusted-local-legacy-test-fixture")
    registry.legacy_model = legacy
    candidate = register(registry, report, reference=file_digest(legacy))
    registry.promote(candidate, expected_generation=0, actor="alice")
    previous = registry.state()["previous"]
    legacy.write_bytes(b"changed-after-promotion")
    registry.rollback(expected_generation=1, actor="alice")
    assert registry.active_path() == registry.root / previous / "model.joblib"
    assert registry.active_path().read_bytes() == b"trusted-local-legacy-test-fixture"


@pytest.mark.parametrize("file", ["model.joblib", "report.json"])
def test_tampering_cannot_promote_and_preserves_selection(registry, report, file):
    candidate = register(registry, report)
    (registry.root / candidate / file).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        registry.promote(candidate, expected_generation=0, actor="alice")
    assert registry.state()["generation"] == 0 and registry.history() == []


def test_corrupt_active_bundle_fails_closed(registry, report):
    candidate = register(registry, report)
    registry.promote(candidate, expected_generation=0, actor="alice")
    (registry.root / candidate / "model.joblib").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        registry.active_path()


def test_rejected_candidate_cannot_promote(registry, report):
    report["updated_macro_f1"] = 0.5
    candidate = register(registry, report)
    with pytest.raises(ValueError, match="non-regression"):
        registry.promote(candidate, expected_generation=0, actor="alice")
    assert registry.state()["generation"] == 0


def test_stale_operator_and_candidate_cannot_change_selection(registry, report):
    first, second = register(registry, report), register(registry, report)
    registry.promote(first, expected_generation=0, actor="alice")
    with pytest.raises(ValueError, match="Stale model selection"):
        registry.rollback(expected_generation=0, actor="bob")
    with pytest.raises(ValueError, match="reference is stale"):
        registry.promote(second, expected_generation=1, actor="bob")
    assert registry.state()["active"] == first and len(registry.history()) == 1


def test_pointer_and_action_are_one_transaction(registry, report):
    candidate = register(registry, report)
    with registry._connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_action BEFORE INSERT ON actions BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        registry.promote(candidate, expected_generation=0, actor="alice")
    assert registry.state() == dict(active=None, previous=None, generation=0)
    assert registry.history() == []


def test_concurrent_promotions_have_exactly_one_winner(registry, report):
    candidates = [register(registry, report) for _ in range(2)]

    def attempt(candidate):
        try:
            registry.promote(candidate, expected_generation=0, actor="test-operator")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, candidates)) == [False, True]
    assert registry.state()["generation"] == 1 and len(registry.history()) == 1


@pytest.mark.parametrize("candidate_id", ["../escape", "a" * 31, "x" * 32])
def test_candidate_ids_cannot_be_paths(registry, candidate_id):
    with pytest.raises(ValueError, match="Invalid candidate"):
        registry.candidate(candidate_id)


def test_blank_operator_is_not_an_audit_identity(registry, report):
    candidate = register(registry, report)
    with pytest.raises(ValueError, match="operator name"):
        registry.promote(candidate, expected_generation=0, actor=" ")
    assert registry.history() == []


@pytest.mark.parametrize("generation", [-1, True, 0.5])
def test_invalid_generation_is_rejected(registry, report, generation):
    candidate = register(registry, report)
    with pytest.raises(ValueError, match="nonnegative integer"):
        registry.promote(candidate, expected_generation=generation, actor="alice")
    assert registry.state()["generation"] == 0


def test_changed_legacy_reference_cannot_promote(registry, report, tmp_path):
    legacy = tmp_path / "legacy.joblib"
    legacy.write_bytes(b"old-reference")
    registry.legacy_model = legacy
    candidate = register(registry, report, reference=file_digest(legacy))
    legacy.write_bytes(b"changed-reference")
    with pytest.raises(ValueError, match="reference integrity changed"):
        registry.promote(candidate, expected_generation=0, actor="alice")
    assert registry.state()["generation"] == 0


def test_registry_cli_lists_promotes_and_rolls_back(registry, report, monkeypatch, capsys):
    from src.model_registry import main

    candidate = register(registry, report)
    prefix = ["model_registry", "--registry", str(registry.root)]
    monkeypatch.setattr(sys, "argv", prefix + ["list"])
    main()
    assert json.loads(capsys.readouterr().out)["candidates"][0]["id"] == candidate
    monkeypatch.setattr(
        sys,
        "argv",
        prefix + ["promote", candidate, "--expected-generation", "0", "--operator", "alice"],
    )
    main()
    assert json.loads(capsys.readouterr().out)["active"] == candidate
    monkeypatch.setattr(
        sys, "argv", prefix + ["rollback", "--expected-generation", "1", "--operator", "alice"]
    )
    main()
    assert json.loads(capsys.readouterr().out)["active"] is None
