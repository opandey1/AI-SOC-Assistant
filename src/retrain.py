"""Retrain the Random Forest from analyst-reviewed false positives."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score

from src.feedback import FeedbackStore
from src.ingest import LABEL_MAP
from src.model_store import create_model_artifact, save_model_artifact


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


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a finite number greater than 0")
    return parsed


def _class_probability(
    probabilities: np.ndarray,
    classes: np.ndarray,
    labels: np.ndarray,
) -> np.ndarray:
    positions = {int(label): index for index, label in enumerate(classes)}
    return np.asarray(
        [probabilities[row, positions[int(label)]] for row, label in enumerate(labels)],
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
) -> RetrainingReport:
    """Fit a new RF with high-trust analyst corrections and save an artifact."""

    if not math.isfinite(feedback_weight) or feedback_weight <= 0:
        raise ValueError("feedback_weight must be a finite value greater than 0.")

    from src.ingest import load_nsl_kdd
    from src.preprocess import preprocess_dataset, transform_connections
    from src.train import score_models, train_isolation_forest, train_random_forest

    project_root = Path(__file__).resolve().parents[1]
    dataset = load_nsl_kdd(
        train_path,
        test_path,
        search_roots=[project_root, Path.cwd()],
    )
    data = preprocess_dataset(dataset, use_smote=use_smote)

    store = FeedbackStore(database_path)
    examples = store.feedback_examples()
    if not examples:
        raise ValueError(
            "No reviewed false positives are available. Mark at least one ticket "
            "as false_positive before retraining."
        )

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
    trained_at = training_time.isoformat(timespec="seconds")
    model_version = training_time.strftime("feedback-%Y%m%dT%H%M%SZ")
    output_path = Path(output_model)
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
        baseline_accuracy=float(accuracy_score(y_true, baseline_models.rf_predictions)),
        updated_accuracy=float(accuracy_score(y_true, updated_models.rf_predictions)),
        baseline_macro_f1=float(
            f1_score(y_true, baseline_models.rf_predictions, average="macro", zero_division=0)
        ),
        updated_macro_f1=float(
            f1_score(y_true, updated_models.rf_predictions, average="macro", zero_division=0)
        ),
        output_model=str(output_path.resolve()),
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
    parser.add_argument("--output", type=Path, default=Path("models") / "soc_model.joblib")
    parser.add_argument("--report", type=Path, default=Path("models") / "retrain_report.json")
    parser.add_argument("--train", type=Path, help="Path to KDDTrain+.txt.")
    parser.add_argument("--test", type=Path, help="Path to KDDTest+.txt.")
    parser.add_argument("--feedback-weight", type=_positive_float, default=25.0)
    parser.add_argument("--isolation-threshold", type=float, default=0.7)
    parser.add_argument("--no-smote", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = retrain_from_feedback(
        database_path=args.database,
        output_model=args.output,
        train_path=args.train,
        test_path=args.test,
        feedback_weight=args.feedback_weight,
        isolation_threshold=args.isolation_threshold,
        use_smote=not args.no_smote,
    )
    report_path = save_report(report, args.report)
    print(json.dumps(asdict(report), indent=2))
    print(f"Saved retraining report to {report_path.resolve()}")


if __name__ == "__main__":
    main()
