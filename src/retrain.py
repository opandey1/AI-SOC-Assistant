"""Retrain the Random Forest from analyst-reviewed false positives."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, recall_score

from src.feedback import FeedbackStore
from src.ingest import LABEL_MAP
from src.model_store import create_model_artifact, save_model_artifact

MINIMUM_FEEDBACK_EXAMPLES = 5
MAXIMUM_FEEDBACK_WEIGHT = 60.0


@dataclass(frozen=True)
class RetrainingReport:
    """Auditable summary of one analyst-feedback model update."""

    model_version: str
    trained_at: str
    feedback_examples: int
    feedback_weight: float
    feedback_predictions_changed: int
    feedback_corrected_before: int
    feedback_corrected_after: int
    mean_corrected_probability_before: float
    mean_corrected_probability_after: float
    evaluation_rows: int
    baseline_accuracy: float
    updated_accuracy: float
    baseline_macro_f1: float
    updated_macro_f1: float
    output_model: str
    baseline_per_class_recall: dict[str, float] = field(default_factory=dict)
    updated_per_class_recall: dict[str, float] = field(default_factory=dict)
    evaluation_support: dict[str, int] = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)


def file_digest(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classification_metrics(labels, predictions) -> dict:
    families = tuple(name for name in LABEL_MAP if name != "unknown")
    ids = [LABEL_MAP[name] for name in families]
    recalls = recall_score(labels, predictions, labels=ids, average=None, zero_division=0)
    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "macro_f1": float(
            f1_score(labels, predictions, labels=ids, average="macro", zero_division=0)
        ),
        "recall": dict(zip(families, map(float, recalls))),
        "support": {name: int(np.sum(np.asarray(labels) == LABEL_MAP[name])) for name in families},
        "unknown_rows": int(np.sum(np.asarray(labels) == LABEL_MAP["unknown"])),
    }


def _code_provenance(root: Path) -> dict:
    files = {
        str(path.relative_to(root)).replace("\\", "/"): file_digest(path)
        for path in sorted((root / "src").glob("*.py"))
    }
    commit, dirty = None, None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        if Path(result.stdout.strip()).resolve() != root.resolve():
            return {"git_commit": None, "git_dirty": None, "source_sha256": files}
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        commit = result.stdout.strip()
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        dirty = bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {"git_commit": commit, "git_dirty": dirty, "source_sha256": files}


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a finite number greater than 0")
    if parsed > MAXIMUM_FEEDBACK_WEIGHT:
        raise argparse.ArgumentTypeError(f"value must not exceed {MAXIMUM_FEEDBACK_WEIGHT}")
    return parsed


def _class_probability(
    probabilities: np.ndarray,
    classes: np.ndarray,
    labels: np.ndarray,
) -> np.ndarray:
    """Look up targets by class ID; an unseen baseline class has probability zero."""

    positions = {int(label): index for index, label in enumerate(classes)}
    return np.asarray(
        [
            probabilities[row, positions[int(label)]] if int(label) in positions else 0.0
            for row, label in enumerate(labels)
        ],
        dtype=float,
    )


def retrain_from_feedback(
    *,
    database_path: str | Path,
    output_model: str | Path,
    train_path: str | Path | None = None,
    test_path: str | Path | None = None,
    feedback_weight: float = 25.0,
    isolation_threshold: float = 0.7,
    use_smote: bool = True,
    minimum_feedback_examples: int = MINIMUM_FEEDBACK_EXAMPLES,
) -> RetrainingReport:
    """Low-level fitter; application/CLI candidate lifecycle is in model_registry.

    Explicit output paths are still supported for library callers. This function alone
    is not a promotion API and can replace that path; never pass an active artifact.
    """

    if not math.isfinite(feedback_weight) or feedback_weight <= 0:
        raise ValueError("feedback_weight must be a finite value greater than 0.")
    if feedback_weight > MAXIMUM_FEEDBACK_WEIGHT:
        raise ValueError(f"feedback_weight must not exceed {MAXIMUM_FEEDBACK_WEIGHT}.")
    if (
        isinstance(minimum_feedback_examples, bool)
        or not isinstance(minimum_feedback_examples, int)
        or minimum_feedback_examples < 1
    ):
        raise ValueError("minimum_feedback_examples must be a positive integer.")

    store = FeedbackStore(database_path)
    examples = store.feedback_examples()
    if not examples:
        raise ValueError("No reviewed false positives are available for retraining.")
    if len(examples) < minimum_feedback_examples:
        raise ValueError(
            f"Retraining requires at least {minimum_feedback_examples} eligible "
            f"corrections; found {len(examples)}."
        )

    from src.ingest import load_nsl_kdd, resolve_dataset_paths
    from src.preprocess import preprocess_dataset, transform_connections
    from src.train import score_models, train_isolation_forest, train_random_forest

    project_root = Path(__file__).resolve().parents[1]
    paths = resolve_dataset_paths(train_path, test_path, search_roots=[project_root, Path.cwd()])
    dataset_hashes = {"train": file_digest(paths.train), "test": file_digest(paths.test)}
    dataset = load_nsl_kdd(
        paths.train,
        paths.test,
        search_roots=[project_root, Path.cwd()],
    )
    data = preprocess_dataset(dataset, use_smote=use_smote)

    feedback_frame = pd.DataFrame([example.raw_record for example in examples])
    _, feedback_scaled = transform_connections(feedback_frame, data)
    feedback_labels = np.asarray(
        [LABEL_MAP[example.corrected_class] for example in examples],
        dtype=int,
    )

    baseline_rf = train_random_forest(data)
    isolation_forest = train_isolation_forest(data)
    baseline_models = score_models(
        baseline_rf,
        isolation_forest,
        data,
        isolation_threshold=isolation_threshold,
    )

    updated_x = np.vstack([data.x_train_balanced, feedback_scaled])
    updated_y = np.concatenate([data.y_train_balanced, feedback_labels])
    sample_weight = np.concatenate(
        [
            np.ones(len(data.x_train_balanced), dtype=float),
            np.full(len(feedback_scaled), feedback_weight, dtype=float),
        ]
    )
    updated_data = replace(
        data,
        x_train_balanced=updated_x,
        y_train_balanced=updated_y,
    )
    updated_rf = train_random_forest(updated_data, sample_weight=sample_weight)
    updated_models = score_models(
        updated_rf,
        isolation_forest,
        data,
        isolation_threshold=isolation_threshold,
        isolation_calibration=baseline_models.isolation_calibration,
    )

    before_prediction = baseline_rf.predict(feedback_scaled)
    after_prediction = updated_rf.predict(feedback_scaled)
    before_probability = _class_probability(
        baseline_rf.predict_proba(feedback_scaled),
        np.asarray(baseline_rf.classes_),
        feedback_labels,
    )
    after_probability = _class_probability(
        updated_rf.predict_proba(feedback_scaled),
        np.asarray(updated_rf.classes_),
        feedback_labels,
    )

    training_time = datetime.now(timezone.utc)
    trained_at = training_time.isoformat(timespec="microseconds")
    model_version = training_time.strftime("feedback-%Y%m%dT%H%M%SZ-") + uuid4().hex
    output_path = Path(output_model)
    if dataset_hashes != {"train": file_digest(paths.train), "test": file_digest(paths.test)}:
        raise ValueError("Dataset files changed during retraining; no artifact was saved.")
    provenance = {
        "datasets": dataset_hashes,
        "code": _code_provenance(project_root),
        "feedback_sha256": sha256(
            json.dumps(
                [asdict(example) for example in examples], sort_keys=True, allow_nan=False
            ).encode()
        ).hexdigest(),
        "feedback_review_ids": [example.review_id for example in examples],
        "minimum_feedback_examples": minimum_feedback_examples,
        "parameters": {
            "random_forest": updated_rf.get_params(),
            "isolation_forest": isolation_forest.get_params(),
            "random_oversampling": use_smote,
            "random_oversampling_seed": 42,
            "isolation_threshold": isolation_threshold,
        },
        "versions": {
            name: version(name)
            for name in ("numpy", "pandas", "scikit-learn", "imbalanced-learn", "joblib")
        },
        "python": sys.version.split()[0],
    }
    baseline_metrics = classification_metrics(data.y_test, baseline_models.rf_predictions)
    updated_metrics = classification_metrics(data.y_test, updated_models.rf_predictions)
    artifact = create_model_artifact(
        data=data,
        models=updated_models,
        model_version=model_version,
        metadata={
            "trained_at": trained_at,
            "feedback_examples": len(examples),
            "feedback_ticket_ids": [example.ticket_id for example in examples],
            "feedback_weight": feedback_weight,
            "random_forest_updated": True,
            "isolation_forest_updated": False,
            "provenance": provenance,
        },
    )
    save_model_artifact(artifact, output_path)

    y_true = data.y_test.to_numpy()
    report = RetrainingReport(
        model_version=model_version,
        trained_at=trained_at,
        feedback_examples=len(examples),
        feedback_weight=float(feedback_weight),
        feedback_predictions_changed=int(np.sum(before_prediction != after_prediction)),
        feedback_corrected_before=int(np.sum(before_prediction == feedback_labels)),
        feedback_corrected_after=int(np.sum(after_prediction == feedback_labels)),
        mean_corrected_probability_before=float(np.mean(before_probability)),
        mean_corrected_probability_after=float(np.mean(after_probability)),
        evaluation_rows=len(y_true),
        baseline_accuracy=baseline_metrics["accuracy"],
        updated_accuracy=updated_metrics["accuracy"],
        baseline_macro_f1=baseline_metrics["macro_f1"],
        updated_macro_f1=updated_metrics["macro_f1"],
        output_model=str(output_path.resolve()),
        baseline_per_class_recall=baseline_metrics["recall"],
        updated_per_class_recall=updated_metrics["recall"],
        evaluation_support=updated_metrics["support"],
        provenance=provenance,
    )
    return report


def save_report(report: RetrainingReport, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update the Random Forest with analyst-reviewed false positives."
    )
    parser.add_argument("--database", type=Path, default=Path("state") / "soc_feedback.db")
    parser.add_argument("--registry", type=Path, default=Path("models") / "registry")
    parser.add_argument("--legacy-model", type=Path, default=Path("models") / "soc_model.joblib")
    parser.add_argument("--train", type=Path, help="Path to KDDTrain+.txt.")
    parser.add_argument("--test", type=Path, help="Path to KDDTest+.txt.")
    parser.add_argument("--feedback-weight", type=_positive_float, default=25.0)
    parser.add_argument("--isolation-threshold", type=float, default=0.7)
    parser.add_argument("--no-smote", action="store_true")
    return parser


def main() -> None:
    from src.model_registry import ModelRegistry

    args = build_parser().parse_args()
    registry = ModelRegistry(args.registry, legacy_model=args.legacy_model)
    candidate = registry.create_candidate(
        database_path=args.database,
        train_path=args.train,
        test_path=args.test,
        feedback_weight=args.feedback_weight,
        isolation_threshold=args.isolation_threshold,
        use_smote=not args.no_smote,
    )
    print(
        json.dumps({"candidate": candidate, "report": registry.report(candidate["id"])}, indent=2)
    )


if __name__ == "__main__":
    main()
