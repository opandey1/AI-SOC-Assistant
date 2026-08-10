"""Evaluation and artifact generation for the AI-SOC-Assistant.

Running this module regenerates every reproducible evaluation artifact:

* ``docs/evaluation/<protocol>/confusion_matrix.png`` - confusion matrix heatmap
* ``docs/evaluation/<protocol>/metrics.*`` - per-class precision/recall/F1
* ``docs/evaluation/<protocol>/shap_drivers.png`` - SHAP contribution plot
* ``docs/evaluation/<protocol>/shap_example_output.json`` - SHAP evidence bundle

By default the model is scored on a stratified hold-out split of ``KDDTrain+``
so the reported numbers describe in-distribution classification quality. Pass
``--use-test-set`` to instead measure cross-distribution generalization against
``KDDTest+`` (which intentionally contains novel attack families).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from src.ingest import CLASS_NAMES, NslKddDataset, load_nsl_kdd

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "evaluation"
HOLDOUT_PROTOCOL_KEY = "holdout"
CROSS_DISTRIBUTION_PROTOCOL_KEY = "cross_distribution"


@dataclass(frozen=True)
class EvaluationReport:
    """Headline classification metrics plus the per-class breakdown."""

    protocol: str
    accuracy: float
    macro_f1: float
    weighted_f1: float
    class_names: list[str]
    confusion_matrix: np.ndarray
    per_class: dict[str, dict[str, float]]
    support: dict[str, int]


def _validate_val_size(val_size: float) -> float:
    value = float(val_size)
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise ValueError("val_size must be a finite value strictly between 0 and 1.")
    return value


def _holdout_fraction(value: str) -> float:
    try:
        return _validate_val_size(float(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _percentage_label(fraction: float) -> str:
    return f"{fraction * 100:.2f}".rstrip("0").rstrip(".")


def _can_stratify_holdout(dataset: NslKddDataset, val_size: float) -> bool:
    """Return whether every class can appear in both sides of the split."""

    from src.ingest import TARGET_COLUMN

    val_size = _validate_val_size(val_size)
    sample_count = len(dataset.train)
    if sample_count < 2:
        return False
    class_counts = dataset.train[TARGET_COLUMN].value_counts()
    class_count = len(class_counts)
    validation_count = math.ceil(sample_count * val_size)
    training_count = sample_count - validation_count
    return bool(
        class_count
        and class_counts.min() >= 2
        and validation_count >= class_count
        and training_count >= class_count
    )


def _holdout_protocol(val_size: float, *, stratified: bool) -> str:
    train_percent = _percentage_label(1.0 - val_size)
    validation_percent = _percentage_label(val_size)
    strategy = "stratified " if stratified else ""
    return f"{strategy}{train_percent}/{validation_percent} hold-out split of KDDTrain+"


def evaluation_output_dir(base_output_dir: str | Path, *, use_test_set: bool) -> Path:
    """Return a protocol-specific artifact directory that prevents overwrites."""

    protocol_key = CROSS_DISTRIBUTION_PROTOCOL_KEY if use_test_set else HOLDOUT_PROTOCOL_KEY
    base = Path(base_output_dir)
    return base if base.name == protocol_key else base / protocol_key


def _build_holdout_dataset(
    dataset: NslKddDataset,
    *,
    random_state: int,
    val_size: float,
) -> NslKddDataset:
    """Split ``KDDTrain+``, stratifying whenever class counts permit it."""

    from sklearn.model_selection import train_test_split

    from src.ingest import TARGET_COLUMN

    val_size = _validate_val_size(val_size)
    if len(dataset.train) < 2:
        raise ValueError("Hold-out evaluation requires at least two training rows.")
    stratify = dataset.train[TARGET_COLUMN] if _can_stratify_holdout(dataset, val_size) else None
    train_part, val_part = train_test_split(
        dataset.train,
        test_size=val_size,
        random_state=random_state,
        stratify=stratify,
    )
    return NslKddDataset(
        train=train_part.reset_index(drop=True),
        test=val_part.reset_index(drop=True),
        paths=dataset.paths,
    )


def evaluate_predictions(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    protocol: str,
) -> EvaluationReport:
    """Compute headline and per-class metrics for a set of predictions."""

    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_recall_fscore_support,
    )

    labels = sorted(set(np.unique(y_true)) | set(np.unique(y_pred)))
    class_names = [CLASS_NAMES[label] for label in labels]

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    per_class = {
        class_names[i]: {
            "precision": float(precision[i]),
            "recall": float(recall[i]),
            "f1": float(f1[i]),
        }
        for i in range(len(labels))
    }

    return EvaluationReport(
        protocol=protocol,
        accuracy=float(accuracy_score(y_true, y_pred)),
        macro_f1=float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
        weighted_f1=float(
            f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0)
        ),
        class_names=class_names,
        confusion_matrix=confusion_matrix(y_true, y_pred, labels=labels),
        per_class=per_class,
        support={class_names[i]: int(support[i]) for i in range(len(labels))},
    )


def _confusion_matrix_title(report: EvaluationReport) -> str:
    """Build a title whose class count and protocol match the actual report."""

    return (
        f"NSL-KDD {len(report.class_names)}-class confusion matrix\n"
        f"{report.protocol} (accuracy {report.accuracy:.2%})"
    )


def save_confusion_matrix(report: EvaluationReport, output_path: Path) -> None:
    """Render the confusion matrix as an annotated heatmap."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    fig, ax = plt.subplots(figsize=(7, 6))
    sns.heatmap(
        report.confusion_matrix,
        annot=True,
        fmt="d",
        cmap="Blues",
        cbar=False,
        xticklabels=report.class_names,
        yticklabels=report.class_names,
        ax=ax,
    )
    ax.set_xlabel("Predicted attack family")
    ax.set_ylabel("True attack family")
    ax.set_title(_confusion_matrix_title(report))
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def report_to_markdown(report: EvaluationReport) -> str:
    """Render the per-class metrics as a GitHub-flavored markdown table."""

    lines = [
        f"## Results ({report.protocol})",
        "",
        f"- **Accuracy:** {report.accuracy:.2%}",
        f"- **Macro F1:** {report.macro_f1:.4f}",
        f"- **Weighted F1:** {report.weighted_f1:.4f}",
        "",
        "| Attack family | Precision | Recall | F1-score | Support |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in report.class_names:
        scores = report.per_class[name]
        lines.append(
            f"| `{name}` | {scores['precision']:.3f} | {scores['recall']:.3f} "
            f"| {scores['f1']:.3f} | {report.support[name]:,} |"
        )
    lines.append("")
    return "\n".join(lines)


def save_metrics(report: EvaluationReport, output_dir: Path) -> None:
    """Persist the metrics as markdown and JSON next to the plot."""

    (output_dir / "metrics.md").write_text(report_to_markdown(report), encoding="utf-8")
    payload = {
        "protocol": report.protocol,
        "accuracy": report.accuracy,
        "macro_f1": report.macro_f1,
        "weighted_f1": report.weighted_f1,
        "per_class": report.per_class,
        "support": report.support,
    }
    (output_dir / "metrics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _select_flagged_index(models: Any, y_true: np.ndarray) -> int:
    """Pick a correctly classified DoS connection to explain, else any anomaly."""

    from src.ingest import LABEL_MAP

    dos = LABEL_MAP["dos"]
    dos_hits = np.flatnonzero((models.rf_predictions == dos) & (y_true == dos))
    if len(dos_hits):
        return int(dos_hits[0])
    flagged = np.flatnonzero(models.fused_anomaly == 1)
    return int(flagged[0]) if len(flagged) else 0


def save_shap_artifacts(data: Any, models: Any, output_dir: Path) -> dict[str, Any]:
    """Generate a SHAP bundle for a flagged connection and plot its drivers."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from src.explain import build_explainer, explain_connection
    from src.ingest import INVERSE_LABEL_MAP, LABEL_MAP

    index = _select_flagged_index(models, data.y_test.to_numpy())
    raw_row = data.x_test.iloc[index]
    row_df = raw_row.to_frame().T
    row_scaled = data.x_test_scaled[index]
    prediction = int(models.rf_predictions[index])

    explainer = build_explainer(models.random_forest)
    bundle = explain_connection(
        explainer=explainer,
        random_forest=models.random_forest,
        raw_row_df=row_df,
        row_scaled=row_scaled,
        prediction=prediction,
        feature_names=data.feature_names,
    )
    rf_anomaly = prediction != LABEL_MAP["normal"]
    isolation_anomaly = bool(models.iso_anomaly[index])
    if rf_anomaly and isolation_anomaly:
        alert_reason = "both"
    elif rf_anomaly:
        alert_reason = "random_forest"
    elif isolation_anomaly:
        alert_reason = "isolation_forest"
    else:
        alert_reason = "none"

    bundle["rf_predicted_class"] = INVERSE_LABEL_MAP.get(
        prediction,
        f"class_{prediction}",
    )
    if alert_reason == "isolation_forest":
        bundle["predicted_class"] = "anomaly"
    bundle["rf_anomaly_confidence"] = float(models.rf_anomaly_confidence[index])
    bundle["isolation_forest_score"] = float(models.iso_scores[index])
    bundle["isolation_risk"] = float(models.iso_normalized[index])
    bundle["isolation_threshold"] = float(models.isolation_threshold)
    bundle["rf_anomaly"] = rf_anomaly
    bundle["isolation_anomaly"] = isolation_anomaly
    bundle["fused_anomaly"] = bool(models.fused_anomaly[index])
    bundle["fused_confidence"] = float(models.fused_confidence[index])
    bundle["alert_reason"] = alert_reason
    bundle["source_ip"] = "192.168.1.47"

    drivers = bundle["top_shap_drivers"]
    features = [d["feature"] for d in drivers][::-1]
    values = [d["shap_value"] for d in drivers][::-1]
    colors = ["#c0392b" if v > 0 else "#2471a3" for v in values]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(features, values, color=colors)
    ax.axvline(0, color="#444444", linewidth=0.8)
    ax.set_xlabel("SHAP value (impact on predicted-class score)")
    ax.set_title(
        f"Top SHAP drivers for RF-predicted {bundle['rf_predicted_class']} "
        f"(confidence {bundle['rf_confidence']:.1%})"
    )
    fig.tight_layout()
    fig.savefig(output_dir / "shap_drivers.png", dpi=150)
    plt.close(fig)

    (output_dir / "shap_example_output.json").write_text(
        json.dumps(bundle, indent=2), encoding="utf-8"
    )
    return bundle


def run_evaluation(args: argparse.Namespace) -> EvaluationReport:
    """Train the detectors and regenerate every evaluation artifact."""

    from src.preprocess import preprocess_dataset
    from src.train import train_models

    project_root = Path(__file__).resolve().parents[1]
    dataset = load_nsl_kdd(
        args.train,
        args.test,
        search_roots=[project_root, Path.cwd()],
        require_test=args.use_test_set,
    )

    if args.use_test_set:
        protocol = "train on KDDTrain+, test on KDDTest+ (cross-distribution)"
        eval_dataset = dataset
    else:
        val_size = _validate_val_size(args.val_size)
        stratified = _can_stratify_holdout(dataset, val_size)
        protocol = _holdout_protocol(val_size, stratified=stratified)
        eval_dataset = _build_holdout_dataset(
            dataset, random_state=args.random_state, val_size=val_size
        )

    data = preprocess_dataset(eval_dataset, use_smote=not args.no_smote)
    models = train_models(data)

    report = evaluate_predictions(data.y_test.to_numpy(), models.rf_predictions, protocol=protocol)

    output_dir = evaluation_output_dir(args.output_dir, use_test_set=args.use_test_set)
    output_dir.mkdir(parents=True, exist_ok=True)
    save_confusion_matrix(report, output_dir / "confusion_matrix.png")
    save_metrics(report, output_dir)
    if not args.skip_shap:
        save_shap_artifacts(data, models, output_dir)

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, help="Path to KDDTrain+.txt.")
    parser.add_argument("--test", type=Path, help="Path to KDDTest+.txt.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Base directory for generated artifacts; a protocol subdirectory is "
            "added automatically (default: docs/evaluation/)."
        ),
    )
    parser.add_argument(
        "--use-test-set",
        action="store_true",
        help="Score against KDDTest+ instead of a hold-out split of KDDTrain+.",
    )
    parser.add_argument(
        "--val-size",
        type=_holdout_fraction,
        default=0.2,
        help="Hold-out fraction strictly between 0 and 1.",
    )
    parser.add_argument("--random-state", type=int, default=42, help="Split seed.")
    parser.add_argument(
        "--no-smote",
        action="store_true",
        help="Disable class balancing (legacy flag name).",
    )
    parser.add_argument("--skip-shap", action="store_true", help="Skip SHAP plot generation.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_evaluation(args)
    print(report_to_markdown(report))
    output_dir = evaluation_output_dir(args.output_dir, use_test_set=args.use_test_set)
    print(f"Artifacts written to {output_dir.resolve()}")


if __name__ == "__main__":
    main()
