"""Evaluation and artifact generation for the AI-SOC-Assistant.

Running this module regenerates every reproducible artifact the README relies on:

* ``docs/confusion_matrix.png`` - 5-class confusion matrix heatmap
* ``docs/metrics.md`` / ``docs/metrics.json`` - per-class precision/recall/F1
* ``docs/shap_drivers.png`` - SHAP feature-contribution plot for a flagged
  connection
* ``docs/shap_example_output.json`` - the matching SHAP evidence bundle

By default the model is scored on a stratified hold-out split of ``KDDTrain+``
so the reported numbers describe in-distribution classification quality. Pass
``--use-test-set`` to instead measure cross-distribution generalization against
``KDDTest+`` (which intentionally contains novel attack families).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from src.ingest import CLASS_NAMES, NslKddDataset, load_nsl_kdd

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "docs"


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


def _build_holdout_dataset(
    dataset: NslKddDataset,
    *,
    random_state: int,
    val_size: float,
) -> NslKddDataset:
    """Split ``KDDTrain+`` into stratified train/validation halves."""

    from sklearn.model_selection import train_test_split

    from src.ingest import TARGET_COLUMN

    train_part, val_part = train_test_split(
        dataset.train,
        test_size=val_size,
        random_state=random_state,
        stratify=dataset.train[TARGET_COLUMN],
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
    ax.set_title(f"NSL-KDD 5-class confusion matrix\n(accuracy {report.accuracy:.2%})")
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
    bundle["isolation_forest_score"] = float(
        models.isolation_forest.decision_function(row_scaled.reshape(1, -1))[0]
    )
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
        f"Top SHAP drivers for predicted `{bundle['predicted_class']}` "
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
    dataset = load_nsl_kdd(args.train, args.test, search_roots=[project_root, Path.cwd()])

    if args.use_test_set:
        protocol = "train on KDDTrain+, test on KDDTest+ (cross-distribution)"
        eval_dataset = dataset
    else:
        protocol = "stratified 80/20 hold-out split of KDDTrain+"
        eval_dataset = _build_holdout_dataset(
            dataset, random_state=args.random_state, val_size=args.val_size
        )

    data = preprocess_dataset(eval_dataset, use_smote=not args.no_smote)
    models = train_models(data)

    report = evaluate_predictions(data.y_test.to_numpy(), models.rf_predictions, protocol=protocol)

    output_dir = Path(args.output_dir)
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
        help="Directory for the generated artifacts (default: docs/).",
    )
    parser.add_argument(
        "--use-test-set",
        action="store_true",
        help="Score against KDDTest+ instead of a hold-out split of KDDTrain+.",
    )
    parser.add_argument("--val-size", type=float, default=0.2, help="Hold-out fraction.")
    parser.add_argument("--random-state", type=int, default=42, help="Split seed.")
    parser.add_argument("--no-smote", action="store_true", help="Disable SMOTE balancing.")
    parser.add_argument("--skip-shap", action="store_true", help="Skip SHAP plot generation.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_evaluation(args)
    print(report_to_markdown(report))
    print(f"Artifacts written to {Path(args.output_dir).resolve()}")


if __name__ == "__main__":
    main()
