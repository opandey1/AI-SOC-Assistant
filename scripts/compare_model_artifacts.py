"""Compare two saved SOC model artifacts within a stated inference scope.

Evidence for E3 lineage recovery (docs/e3_protocol.md §8.3): a refit from claimed
inputs must equal the live artifact on everything that affects inference. The scope
compared here (``SCOPE``) is:

- the artifact's feature names, isolation calibration, threshold and format;
- every parameter and fitted attribute of the encoder and the scaler;
- every parameter and attribute of both forests and of each of their trees, and every
  tree's structure, split features, thresholds, leaf values and node counts;
- end-to-end scores for synthetic raw records, through the application's own
  ``transform_connections`` and ``score_connection``, single-threaded.

Node impurity is a training statistic that prediction never reads, so its differences
are reported but do not count. joblib artifacts are executable pickles: run this only
on trusted local files. Both artifacts are loaded read-only, and nothing is written.

Usage: python -m scripts.compare_model_artifacts LIVE_ARTIFACT REFIT_ARTIFACT
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.ingest import CATEGORICAL_FEATURES, MODEL_INPUT_COLUMNS
from src.model_store import load_model_artifact
from src.preprocess import transform_connections
from src.train import score_connection

SCOPE = (
    "artifact feature names, isolation calibration, threshold and format version",
    "every parameter and fitted attribute of the encoder and the scaler",
    "every parameter and attribute of both forests and of each tree",
    "each tree's structure, split features, thresholds, leaf values and node counts",
    "end-to-end scores for synthetic raw records via transform_connections and "
    "score_connection, single-threaded",
)
# Tree arrays that decide an output; impurity is compared separately and not counted.
TREE_ARRAYS = (
    "children_left",
    "children_right",
    "feature",
    "threshold",
    "value",
    "n_node_samples",
    "weighted_n_node_samples",
)
UNSEEN_CATEGORY = "unseen-category"


def _same(left: Any, right: Any) -> bool:
    """Value equality across arrays, containers, dataclasses and estimators."""
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        left, right = np.asarray(left), np.asarray(right)
        floating = left.dtype.kind == "f" and right.dtype.kind == "f"
        return left.shape == right.shape and np.array_equal(left, right, equal_nan=floating)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if hasattr(left, "get_params") and hasattr(right, "get_params"):
        return type(left) is type(right) and _same(
            left.get_params(deep=False), right.get_params(deep=False)
        )
    if is_dataclass(left) and is_dataclass(right):
        return type(left) is type(right) and _same(asdict(left), asdict(right))
    if isinstance(left, float) and isinstance(right, float) and np.isnan(left):
        return bool(np.isnan(right))
    return bool(left == right)


def _attribute_differences(left: Any, right: Any, skip: frozenset = frozenset()) -> list[str]:
    """Names of parameters or attributes (``vars``) that differ between two objects."""
    if type(left) is not type(right):
        return ["<type>"]
    names = (set(vars(left)) | set(vars(right))) - skip
    return sorted(
        name
        for name in names
        if name not in vars(left)
        or name not in vars(right)
        or not _same(vars(left)[name], vars(right)[name])
    )


def _tree_differences(left: Any, right: Any) -> list[int]:
    if len(left.estimators_) != len(right.estimators_):
        return [-1]
    return [
        index
        for index, (a, b) in enumerate(zip(left.estimators_, right.estimators_))
        if _attribute_differences(a, b, frozenset({"tree_"}))
        or not all(_same(getattr(a.tree_, name), getattr(b.tree_, name)) for name in TREE_ARRAYS)
    ]


def _impurity_differences(left: Any, right: Any) -> dict:
    pairs = [
        (a.tree_.impurity, b.tree_.impurity) for a, b in zip(left.estimators_, right.estimators_)
    ]
    trees = [index for index, (a, b) in enumerate(pairs) if not _same(a, b)]
    same_shape = [index for index in trees if pairs[index][0].shape == pairs[index][1].shape]
    largest = max(
        (float(np.max(np.abs(pairs[i][0] - pairs[i][1]))) for i in same_shape), default=0.0
    )
    return {
        "trees": trees,
        "largest_absolute_difference": largest,
        "different_structure": [index for index in trees if index not in same_shape],
    }


def probe_records(artifact: Any, rows: int = 300, seed: int = 0) -> pd.DataFrame:
    """Synthetic raw records: known categories (and one unseen value), finite numbers."""
    rng = np.random.default_rng(seed)
    frame = {}
    categories = dict(zip(CATEGORICAL_FEATURES, artifact.encoder.categories_))
    for column in MODEL_INPUT_COLUMNS:
        if column in categories:
            choices = [str(value) for value in categories[column]] + [UNSEEN_CATEGORY]
            frame[column] = rng.choice(choices, size=rows)
        else:
            frame[column] = np.round(rng.exponential(scale=10.0, size=rows), 3)
    return pd.DataFrame(frame, columns=MODEL_INPUT_COLUMNS)


def _scores(artifact: Any, records: pd.DataFrame) -> Any:
    try:
        _, scaled = transform_connections(records, artifact)
        return {
            "scaled": scaled,
            "rf_probabilities": artifact.random_forest.predict_proba(scaled),
            "connections": [asdict(score_connection(artifact, row)) for row in scaled],
        }
    except Exception as error:  # a failure on one side only is itself a difference
        return {"error": f"{type(error).__name__}: {error}"}


def compare(live_path: Path, refit_path: Path, *, probe_rows: int = 300) -> dict:
    live, refit = load_model_artifact(live_path), load_model_artifact(refit_path)
    forests = (("rf", "random_forest"), ("if", "isolation_forest"))
    checks = {
        "feature_names": _same(live.feature_names, refit.feature_names),
        "isolation_calibration": _same(live.isolation_calibration, refit.isolation_calibration),
        "isolation_threshold": _same(live.isolation_threshold, refit.isolation_threshold),
        "format_version": live.format_version == refit.format_version,
    }
    differences = {
        "encoder": _attribute_differences(live.encoder, refit.encoder),
        "scaler": _attribute_differences(live.scaler, refit.scaler),
    }
    for short, name in forests:
        left, right = getattr(live, name), getattr(refit, name)
        differences[f"{short}_attributes"] = _attribute_differences(
            left, right, frozenset({"estimators_"})
        )
        differences[f"{short}_trees"] = _tree_differences(left, right)
    for name, found in differences.items():
        checks[name] = not found

    # Parallel prediction sums trees in thread order and can differ in the last place
    # between calls on one model, so the end-to-end probe runs single-threaded.
    for artifact in (live, refit):
        for _, name in forests:
            getattr(artifact, name).set_params(n_jobs=1)
    records = probe_records(live, probe_rows)
    live_scores, refit_scores = _scores(live, records), _scores(refit, records)
    checks["end_to_end_scores"] = "error" not in live_scores and _same(live_scores, refit_scores)

    checks = {name: bool(value) for name, value in checks.items()}
    notes = {
        "live_sha256": sha256(Path(live_path).read_bytes()).hexdigest(),
        "refit_sha256": sha256(Path(refit_path).read_bytes()).hexdigest(),
        "model_versions": [live.model_version, refit.model_version],
        "rf_class_dtypes": [
            str(live.random_forest.classes_.dtype),
            str(refit.random_forest.classes_.dtype),
        ],
        "differences": differences,
        "probe_rows": probe_rows,
        "probe_errors": [
            scores.get("error") for scores in (live_scores, refit_scores) if "error" in scores
        ],
        "rf_impurity": _impurity_differences(live.random_forest, refit.random_forest),
        "if_impurity": _impurity_differences(live.isolation_forest, refit.isolation_forest),
    }
    return {"equal": all(checks.values()), "scope": list(SCOPE), "checks": checks, "notes": notes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("live", type=Path)
    parser.add_argument("refit", type=Path)
    parser.add_argument("--probe-rows", type=int, default=300)
    args = parser.parse_args()
    result = compare(args.live, args.refit, probe_rows=args.probe_rows)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
