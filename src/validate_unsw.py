"""Schema-harmonized NSL-KDD to UNSW-NB15 transfer benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.ingest import TARGET_COLUMN, load_nsl_kdd

TRANSFER_CLASS_NAMES = ["normal", "dos", "probe", "r2l", "u2r"]
COMMON_FEATURE_MAP = {
    "duration": "dur",
    "protocol_type": "proto",
    "service": "service",
    "flag": "state",
    "src_bytes": "sbytes",
    "dst_bytes": "dbytes",
    "land": "is_sm_ips_ports",
}
CATEGORICAL_COMMON_FEATURES = ["protocol_type", "service", "flag"]
NUMERIC_COMMON_FEATURES = [
    feature for feature in COMMON_FEATURE_MAP if feature not in CATEGORICAL_COMMON_FEATURES
]
UNSW_FAMILY_MAP = {
    "Normal": "normal",
    "DoS": "dos",
    "Worms": "dos",
    "Reconnaissance": "probe",
    "Analysis": "probe",
    "Fuzzers": "probe",
    "Exploits": "r2l",
    "Backdoor": "r2l",
    "Backdoors": "r2l",
    "Shellcode": "u2r",
}


@dataclass(frozen=True)
class BenchmarkResult:
    """Metrics and diagnostics for one evaluation domain."""

    rows: int
    accuracy: float
    balanced_accuracy: float
    macro_f1: float
    binary_accuracy: float
    binary_f1: float
    confusion_matrix: list[list[int]]
    per_class: dict[str, dict[str, float | int]]


def _make_encoder() -> OneHotEncoder:
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def make_transfer_pipeline(*, random_state: int = 42) -> Pipeline:
    """Create the reduced common-schema model used for both evaluation domains."""

    transformer = ColumnTransformer(
        [
            ("categorical", _make_encoder(), CATEGORICAL_COMMON_FEATURES),
            ("numeric", StandardScaler(), NUMERIC_COMMON_FEATURES),
        ],
        remainder="drop",
    )
    classifier = RandomForestClassifier(
        n_estimators=200,
        max_depth=25,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=random_state,
        n_jobs=-1,
    )
    return Pipeline([("preprocess", transformer), ("classifier", classifier)])


def harmonize_unsw(unsw: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, dict[str, int]]:
    """Map UNSW fields and supported attack categories into the NSL taxonomy."""

    required = [*COMMON_FEATURE_MAP.values(), "attack_cat"]
    missing = [column for column in required if column not in unsw.columns]
    if missing:
        raise ValueError(f"UNSW-NB15 file is missing required columns: {', '.join(missing)}")

    categories = unsw["attack_cat"].fillna("Normal").astype(str).str.strip()
    mapped_target = categories.map(UNSW_FAMILY_MAP)
    excluded_counts = categories[mapped_target.isna()].value_counts().sort_index().to_dict()
    supported = mapped_target.notna()
    harmonized = unsw.loc[supported, list(COMMON_FEATURE_MAP.values())].rename(
        columns={source: target for target, source in COMMON_FEATURE_MAP.items()}
    )
    harmonized = harmonized.reset_index(drop=True)
    target = mapped_target.loc[supported].reset_index(drop=True)

    for column in CATEGORICAL_COMMON_FEATURES:
        harmonized[column] = harmonized[column].fillna("unknown").astype(str)
    for column in NUMERIC_COMMON_FEATURES:
        harmonized[column] = pd.to_numeric(harmonized[column], errors="coerce")
    if harmonized[NUMERIC_COMMON_FEATURES].isna().any().any():
        raise ValueError("UNSW-NB15 common numeric fields contain missing or invalid values.")
    return harmonized, target, {str(key): int(value) for key, value in excluded_counts.items()}


def evaluate_predictions(y_true: pd.Series, y_prediction: np.ndarray) -> BenchmarkResult:
    labels = TRANSFER_CLASS_NAMES
    precision, recall, f1_values, support = precision_recall_fscore_support(
        y_true,
        y_prediction,
        labels=labels,
        zero_division=0,
    )
    per_class = {
        label: {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1_values[index]),
            "support": int(support[index]),
        }
        for index, label in enumerate(labels)
    }
    binary_true = np.where(np.asarray(y_true) == "normal", "normal", "attack")
    binary_prediction = np.where(np.asarray(y_prediction) == "normal", "normal", "attack")
    return BenchmarkResult(
        rows=len(y_true),
        accuracy=float(accuracy_score(y_true, y_prediction)),
        balanced_accuracy=float(balanced_accuracy_score(y_true, y_prediction)),
        macro_f1=float(
            f1_score(y_true, y_prediction, labels=labels, average="macro", zero_division=0)
        ),
        binary_accuracy=float(accuracy_score(binary_true, binary_prediction)),
        binary_f1=float(
            f1_score(
                binary_true,
                binary_prediction,
                labels=["attack"],
                average="macro",
                zero_division=0,
            )
        ),
        confusion_matrix=confusion_matrix(y_true, y_prediction, labels=labels).tolist(),
        per_class=per_class,
    )


def categorical_novelty(
    nsl_train: pd.DataFrame,
    unsw_features: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    diagnostics: dict[str, dict[str, Any]] = {}
    for feature in CATEGORICAL_COMMON_FEATURES:
        known = set(nsl_train[feature].astype(str))
        values = unsw_features[feature].astype(str)
        unseen = ~values.isin(known)
        diagnostics[feature] = {
            "nsl_categories": len(known),
            "unsw_categories": int(values.nunique()),
            "unseen_rows": int(unseen.sum()),
            "unseen_rate": float(unseen.mean()),
        }
    return diagnostics


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result_dict(result: BenchmarkResult) -> dict[str, Any]:
    return {
        "rows": result.rows,
        "accuracy": result.accuracy,
        "balanced_accuracy": result.balanced_accuracy,
        "macro_f1": result.macro_f1,
        "binary_accuracy": result.binary_accuracy,
        "binary_f1": result.binary_f1,
        "confusion_matrix": result.confusion_matrix,
        "per_class": result.per_class,
    }


def _percentage(value: float) -> str:
    return f"{100 * value:.2f}%"


def report_markdown(payload: dict[str, Any]) -> str:
    holdout = payload["nsl_holdout"]
    transfer = payload["unsw_transfer"]
    lines = [
        "# UNSW-NB15 Transfer Validation",
        "",
        "A Random Forest was trained only on NSL-KDD using the seven shared or "
        "closest-compatible flow fields listed below. No UNSW-NB15 labels were used "
        "for training, feature selection, threshold selection, or tuning.",
        "",
        "| Evaluation | Rows | Accuracy | Balanced accuracy | Macro F1 | Binary attack F1 |",
        "|---|---:|---:|---:|---:|---:|",
        (
            f"| NSL-KDD stratified hold-out | {holdout['rows']:,} | "
            f"{_percentage(holdout['accuracy'])} | "
            f"{_percentage(holdout['balanced_accuracy'])} | "
            f"{_percentage(holdout['macro_f1'])} | "
            f"{_percentage(holdout['binary_f1'])} |"
        ),
        (
            f"| UNSW-NB15 zero-tuning transfer | {transfer['rows']:,} | "
            f"{_percentage(transfer['accuracy'])} | "
            f"{_percentage(transfer['balanced_accuracy'])} | "
            f"{_percentage(transfer['macro_f1'])} | "
            f"{_percentage(transfer['binary_f1'])} |"
        ),
        "",
        "## Transfer Per-Class Metrics",
        "",
        "| Class | Precision | Recall | F1 | Support |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in TRANSFER_CLASS_NAMES:
        metrics = transfer["per_class"][label]
        lines.append(
            f"| {label} | {_percentage(metrics['precision'])} | "
            f"{_percentage(metrics['recall'])} | {_percentage(metrics['f1'])} | "
            f"{metrics['support']:,} |"
        )

    lines.extend(
        [
            "",
            "## Harmonized Feature Contract",
            "",
            "| NSL-KDD field | UNSW-NB15 field | Compatibility |",
            "|---|---|---|",
            "| `duration` | `dur` | Direct flow-duration measure |",
            "| `protocol_type` | `proto` | Direct protocol category |",
            "| `service` | `service` | Direct service category |",
            "| `flag` | `state` | Approximate connection-state proxy |",
            "| `src_bytes` | `sbytes` | Direct source-to-destination bytes |",
            "| `dst_bytes` | `dbytes` | Direct destination-to-source bytes |",
            "| `land` | `is_sm_ips_ports` | Closest same-endpoint indicator |",
            "",
            "## Label Mapping",
            "",
            "- Normal -> normal",
            "- DoS and Worms -> dos",
            "- Reconnaissance, Analysis, and Fuzzers -> probe",
            "- Exploits and Backdoor -> r2l",
            "- Shellcode -> u2r",
            "- Generic -> excluded because the NSL-KDD five-family taxonomy has no defensible equivalent",
            "",
            f"Excluded rows: {payload['excluded_rows']:,} "
            f"({json.dumps(payload['excluded_categories'], sort_keys=True)}).",
            "",
            "## Interpretation",
            "",
            "The transfer score is expected to be materially lower than the in-domain hold-out. "
            "That gap measures dataset, capture, feature-definition, and attack-taxonomy shift; "
            "it is not presented as a production deployment score. The result establishes a "
            "reproducible external baseline and identifies where a live Zeek/SIEM feature adapter "
            "and modern training data are required.",
            "",
            "Dataset: [UNSW-NB15, UNSW Canberra](https://research.unsw.edu.au/projects/unsw-nb15-dataset).",
        ]
    )
    return "\n".join(lines) + "\n"


def save_confusion_matrices(
    holdout: BenchmarkResult,
    transfer: BenchmarkResult,
    output_path: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="white")
    figure, axes = plt.subplots(1, 2, figsize=(15, 6), constrained_layout=True)
    for axis, result, title in (
        (axes[0], holdout, "NSL-KDD hold-out"),
        (axes[1], transfer, "UNSW-NB15 transfer"),
    ):
        sns.heatmap(
            np.asarray(result.confusion_matrix),
            annot=True,
            fmt="d",
            cmap="YlGnBu",
            cbar=False,
            xticklabels=TRANSFER_CLASS_NAMES,
            yticklabels=TRANSFER_CLASS_NAMES,
            ax=axis,
        )
        axis.set_title(title)
        axis.set_xlabel("Predicted class")
        axis.set_ylabel("True class")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def run_benchmark(
    *,
    nsl_train_path: str | Path | None,
    unsw_path: str | Path,
    output_dir: str | Path,
    random_state: int = 42,
) -> dict[str, Any]:
    """Run the in-domain control and zero-tuning external transfer evaluation."""

    project_root = Path(__file__).resolve().parents[1]
    dataset = load_nsl_kdd(
        train_path=nsl_train_path,
        search_roots=[project_root, Path.cwd()],
        require_test=False,
    )
    nsl_train = dataset.train[dataset.train[TARGET_COLUMN].isin(TRANSFER_CLASS_NAMES)].copy()
    nsl_features = nsl_train[list(COMMON_FEATURE_MAP)]
    nsl_target = nsl_train[TARGET_COLUMN]
    train_x, holdout_x, train_y, holdout_y = train_test_split(
        nsl_features,
        nsl_target,
        test_size=0.2,
        random_state=random_state,
        stratify=nsl_target,
    )

    holdout_model = make_transfer_pipeline(random_state=random_state)
    holdout_model.fit(train_x, train_y)
    holdout_result = evaluate_predictions(holdout_y, holdout_model.predict(holdout_x))

    unsw_file = Path(unsw_path)
    unsw = pd.read_csv(unsw_file)
    unsw_features, unsw_target, excluded_categories = harmonize_unsw(unsw)
    transfer_model = make_transfer_pipeline(random_state=random_state)
    transfer_model.fit(nsl_features, nsl_target)
    transfer_result = evaluate_predictions(
        unsw_target,
        transfer_model.predict(unsw_features),
    )

    payload = {
        "protocol": "NSL-KDD common-feature Random Forest -> UNSW-NB15 zero-tuning transfer",
        "random_state": random_state,
        "common_features": COMMON_FEATURE_MAP,
        "label_mapping": UNSW_FAMILY_MAP,
        "source_rows": len(nsl_train),
        "target_rows_total": len(unsw),
        "target_rows_evaluated": len(unsw_target),
        "excluded_rows": len(unsw) - len(unsw_target),
        "excluded_categories": excluded_categories,
        "target_sha256": sha256_file(unsw_file),
        "categorical_novelty": categorical_novelty(nsl_features, unsw_features),
        "nsl_holdout": _result_dict(holdout_result),
        "unsw_transfer": _result_dict(transfer_result),
    }

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "metrics.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "metrics.md").write_text(report_markdown(payload), encoding="utf-8")
    save_confusion_matrices(
        holdout_result,
        transfer_result,
        destination / "confusion_matrix.png",
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure zero-tuning NSL-KDD to UNSW-NB15 transfer performance."
    )
    parser.add_argument("--train", type=Path, help="Path to KDDTrain+.txt.")
    parser.add_argument(
        "--unsw",
        type=Path,
        default=Path("data") / "UNSW_NB15_testing-set.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs") / "evaluation" / "unsw_transfer",
    )
    parser.add_argument("--random-state", type=int, default=42)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_benchmark(
        nsl_train_path=args.train,
        unsw_path=args.unsw,
        output_dir=args.output_dir,
        random_state=args.random_state,
    )
    transfer = result["unsw_transfer"]
    print(f"UNSW-NB15 rows evaluated: {transfer['rows']:,}")
    print(f"Transfer accuracy: {_percentage(transfer['accuracy'])}")
    print(f"Transfer macro F1: {_percentage(transfer['macro_f1'])}")
    print(f"Binary attack F1: {_percentage(transfer['binary_f1'])}")
    print(f"Artifacts: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
