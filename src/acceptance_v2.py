"""Rule v2 candidate acceptance checks (E3 protocol, ``docs/e3_protocol.md`` §2-§5).

``decide`` is a pure function over evaluation rows, per-row predictions and a
calibration record. It fits nothing, loads no model and never touches the registry.
Promotion keeps using rule v1 (``acceptance_reasons`` in ``src/model_registry.py``)
until v2 is activated under protocol §9.

This file's SHA-256 is the protocol's ``checker_id`` (§3), so any change to it needs
the §8.1 fixtures (``tests/test_acceptance_v2.py``) to pass again.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, recall_score

FAMILIES = ("normal", "dos", "probe", "r2l", "u2r")
RECALL_FAMILIES = ("normal", "dos", "probe", "r2l")
METRICS = ("accuracy", "macro_f1") + tuple(f"recall_{family}" for family in RECALL_FAMILIES)
CAPS = {
    "accuracy": 0.010,
    "macro_f1": 0.020,
    "recall_normal": 0.020,
    "recall_dos": 0.020,
    "recall_probe": 0.020,
    "recall_r2l": 0.030,
}
DECISION_SEEDS = (42, 2001, 2002, 2003, 2004)
DEPLOYMENT_SEED = 42
CALIBRATION_SEEDS = tuple(range(3001, 3011))
ORACLE_SEEDS = tuple(range(4001, 4011))
MINIMUM_SUPPORT = 30
INPUT_WIDTH = 41  # NSL-KDD model inputs per row (src.ingest.MODEL_INPUT_COLUMNS)
TOLERANCE = 1e-12
CALIBRATION_SCHEMA = "e3-calibration/1"
CALIBRATION_FIELDS = frozenset(
    {
        "schema",
        "train_id",
        "eval_id",
        "recipe_id",
        "seeds",
        "sd",
        "caps",
        "allowance_average",
        "allowance_deployed",
        "zero_spread",
        "created_at",
        "calibration_id",
    }
)
LIVE = ("live", None)


def _plain(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):  # tolist() also unwraps zero-dimensional arrays
        return _plain(value.tolist())
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def canonical_json(value: Any) -> bytes:
    """§2: UTF-8, sorted keys, compact separators, shortest float form, no NaN."""
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return sha256(canonical_json(value)).hexdigest()


def input_hash(inputs: Sequence[Any]) -> str:
    return digest(list(inputs))


def row_hash(inputs: Sequence[Any], label: str) -> str:
    return digest([*inputs, label])


def dataset_identity(rows: Sequence[tuple[Sequence[Any], str]]) -> str:
    """``train_id``/``eval_id``: SHA-256 of the ordered row hashes."""
    return digest([row_hash(inputs, label) for inputs, label in rows])


def prediction_digest(labels: Sequence[str]) -> str:
    """SHA-256 of a prediction set: the canonical JSON array of its labels in E's order."""
    return digest(list(labels))


def checker_id() -> str:
    return sha256(Path(__file__).read_bytes()).hexdigest()


def allowance_average(sd: float, cap: float) -> float:
    return min(2.0 * sd / math.sqrt(5.0), cap)


def allowance_deployed(sd: float, cap: float) -> float:
    return min(2.0 * sd, cap)


def seed_spread(values: Sequence[float]) -> float:
    """§4: ``numpy.std(ddof=1)`` over float64 values, in seed order."""
    return float(np.std(np.asarray(values, dtype=np.float64), ddof=1))


def calibration_record(
    *, train_id: str, eval_id: str, recipe_id: str, sd: Mapping[str, float], created_at: str
) -> dict:
    spreads = {metric: float(sd[metric]) for metric in METRICS}
    record = {
        "schema": CALIBRATION_SCHEMA,
        "train_id": train_id,
        "eval_id": eval_id,
        "recipe_id": recipe_id,
        "seeds": list(CALIBRATION_SEEDS),
        "sd": spreads,
        "caps": dict(CAPS),
        "allowance_average": {m: allowance_average(spreads[m], CAPS[m]) for m in METRICS},
        "allowance_deployed": {m: allowance_deployed(spreads[m], CAPS[m]) for m in METRICS},
        "zero_spread": sorted(m for m in METRICS if spreads[m] == 0.0),
        "created_at": created_at,
    }
    record["calibration_id"] = digest(record)
    return record


def calibration_from_metrics(
    metrics_by_seed: Mapping[int, Mapping[str, float]], **identities: str
) -> dict:
    """Build a record from the B(c) metrics on E for every calibration seed c."""
    missing = [seed for seed in CALIBRATION_SEEDS if seed not in metrics_by_seed]
    if missing:
        raise ValueError(f"Calibration seeds without metrics: {missing}")
    spreads = {
        metric: seed_spread([metrics_by_seed[seed][metric] for seed in CALIBRATION_SEEDS])
        for metric in METRICS
    }
    return calibration_record(sd=spreads, **identities)


def _number(value: Any) -> bool:
    """A real, finite JSON number: never a boolean, and never too large for a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def calibration_is_valid(record: Any) -> bool:
    """§4 validity: identity, seeds, keys, finite SDs, policy caps, recomputed limits."""
    try:
        if not isinstance(record, Mapping) or set(record) != CALIBRATION_FIELDS:
            return False
        body = {key: value for key, value in record.items() if key != "calibration_id"}
        if record["schema"] != CALIBRATION_SCHEMA or record["calibration_id"] != digest(body):
            return False
        if not all(isinstance(record[key], str) for key in ("train_id", "eval_id", "recipe_id")):
            return False
        seeds = list(record["seeds"])
        if seeds != list(CALIBRATION_SEEDS) or not all(type(seed) is int for seed in seeds):
            return False
        tables = ("sd", "caps", "allowance_average", "allowance_deployed")
        if any(
            not isinstance(record[key], Mapping)
            or set(record[key]) != set(METRICS)
            or not all(_number(value) for value in record[key].values())
            for key in tables
        ):
            return False
        sd = record["sd"]
        if any(sd[metric] < 0 for metric in METRICS):
            return False
        if any(record["caps"][metric] != CAPS[metric] for metric in METRICS):
            return False
        for metric in METRICS:
            if record["allowance_average"][metric] != allowance_average(sd[metric], CAPS[metric]):
                return False
            if record["allowance_deployed"][metric] != allowance_deployed(sd[metric], CAPS[metric]):
                return False
        return list(record["zero_spread"]) == sorted(m for m in METRICS if sd[m] == 0)
    except (KeyError, OverflowError, TypeError, ValueError):
        return False


def evaluation_metrics(truth: Sequence[str], predicted: Sequence[str]) -> dict[str, float]:
    """The six §2 metrics, computed as ``classification_metrics`` in ``src/retrain.py``."""
    labels = list(FAMILIES)
    recalls = recall_score(truth, predicted, labels=labels, average=None, zero_division=0)
    metrics = {
        "accuracy": float(accuracy_score(truth, predicted)),
        "macro_f1": float(
            f1_score(truth, predicted, labels=labels, average="macro", zero_division=0)
        ),
    }
    for family, value in zip(FAMILIES, recalls):
        if family != "u2r":
            metrics[f"recall_{family}"] = float(value)
    return metrics


@dataclass(frozen=True)
class PredictionSet:
    """Per-row family predictions on E, bound to E's identity and their own SHA-256."""

    labels: tuple[str, ...]
    eval_id: str
    sha256: str

    @classmethod
    def bind(cls, labels: Sequence[str], eval_id: str) -> "PredictionSet":
        labels = tuple(_plain(list(labels)))
        return cls(labels=labels, eval_id=eval_id, sha256=prediction_digest(labels))


@dataclass(frozen=True)
class Decision:
    outcome: str
    reasons: tuple[str, ...]
    details: dict = field(default_factory=dict)


def _name(key: tuple[str, int | None]) -> str:
    role, seed = key
    return role if seed is None else f"{role}:{seed}"


def _error(step: str, reasons: list[str], **details: Any) -> Decision:
    return Decision("error", tuple(reasons), {"step": step, **details})


def _is_pair(row: Any) -> bool:
    return isinstance(row, (list, tuple)) and len(row) == 2


def _valid_label(label: Any) -> bool:
    """A family name; the type is checked first, so arrays never reach a comparison."""
    return isinstance(label, str) and label in FAMILIES


def _valid_input(value: Any) -> bool:
    """A scalar that canonical JSON encodes unambiguously: UTF-8 text, an integer or a
    finite float. Refused: booleans, missing values, containers, arrays of any shape,
    non-finite numbers, text that UTF-8 cannot encode (lone surrogates) and integers
    longer than the runtime allows to be written in decimal."""
    if isinstance(value, np.ndarray):
        return False
    value = _plain(value)
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    if isinstance(value, int):
        try:
            str(value)  # JSON writes integers in decimal; see sys.get_int_max_str_digits
        except ValueError:
            return False
        return True
    return False


def _well_formed(row: Any) -> bool:
    """An (inputs, label) pair whose inputs are exactly INPUT_WIDTH valid scalars."""
    if not _is_pair(row) or not isinstance(row[0], (list, tuple, np.ndarray)):
        return False
    inputs = row[0]
    if isinstance(inputs, np.ndarray) and inputs.ndim != 1:
        return False
    return len(inputs) == INPUT_WIDTH and all(_valid_input(value) for value in inputs)


def _bound(prediction: Any, rows: int, eval_id: str) -> bool:
    try:
        labels = prediction.labels
        return (
            len(labels) == rows
            and prediction.eval_id == eval_id
            and prediction_digest(labels) == prediction.sha256
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _passes(change: float, allowance: float) -> bool:
    return change >= -allowance - TOLERANCE


def decide(
    rows: Sequence[tuple[Sequence[Any], str]] | None,
    calibration: Mapping[str, Any] | None,
    predictions: Mapping[tuple[str, int | None], PredictionSet | None] | None,
    *,
    train_id: str,
    recipe_id: str,
    live_exists: bool,
) -> Decision:
    """Protocol §5: preconditions, support, average, deployed, live and U2R checks."""
    required = [(role, seed) for role in ("candidate", "baseline") for seed in DECISION_SEEDS]
    if live_exists:
        required.append(LIVE)
    # Anything that is not a mapping holds no usable prediction set.
    predictions = predictions if isinstance(predictions, Mapping) else {}

    # Check 1 runs step by step; the first failing step ends the decision.
    missing = [_name(key) for key in required if predictions.get(key) is None]
    reasons = [
        code
        for code, absent in (
            ("missing_evaluation_rows", rows is None),
            ("calibration_missing", calibration is None),
            ("missing_predictions", bool(missing)),
        )
        if absent
    ]
    if reasons:
        return _error("presence", reasons, missing_predictions=missing)

    try:
        rows = list(rows)
    except TypeError:
        return _error("evaluation_population", ["invalid_evaluation_population"])
    # Before anything is hashed or scored: every row is an (inputs, label) pair with
    # exactly INPUT_WIDTH valid scalar inputs, and every label is one of the families.
    reasons = []
    if any(_is_pair(row) and not _valid_label(row[1]) for row in rows):
        reasons.append("invalid_evaluation_label")
    malformed = [index for index, row in enumerate(rows) if not _well_formed(row)]
    if not rows or malformed:
        reasons.append("invalid_evaluation_population")
    if reasons:
        return _error("evaluation_population", reasons, malformed_rows=malformed[:20])

    if not calibration_is_valid(calibration):
        return _error("calibration_validity", ["calibration_invalid"])
    try:
        eval_id = dataset_identity(rows)
    except (TypeError, ValueError):  # backstop: validated rows should always encode
        return _error("evaluation_population", ["invalid_evaluation_population"])
    current = {"train_id": train_id, "eval_id": eval_id, "recipe_id": recipe_id}
    if any(calibration[key] != value for key, value in current.items()):
        return _error("currency", ["calibration_stale"], current=current)
    unbound = [_name(key) for key in required if not _bound(predictions[key], len(rows), eval_id)]
    if unbound:
        return _error("bindings", ["evidence_mismatch"], predictions=unbound)
    invalid = [
        _name(key)
        for key in required
        if not all(_valid_label(label) for label in predictions[key].labels)
    ]
    if invalid:
        return _error("values", ["invalid_prediction"], predictions=invalid)
    truth = np.asarray([label for _, label in rows])
    metrics = {
        key: evaluation_metrics(truth, np.asarray(predictions[key].labels)) for key in required
    }
    if not all(
        isinstance(value, float) and math.isfinite(value) and 0.0 <= value <= 1.0
        for values in metrics.values()
        for value in values.values()
    ):
        return _error("values", ["invalid_metric"])

    # Check 2: classes below the minimum support skip their recall checks.
    support = Counter(label for _, label in rows)
    skipped = [family for family in RECALL_FAMILIES if support[family] < MINIMUM_SUPPORT]
    reasons = [f"insufficient_support_{family}" for family in skipped]
    checked = [m for m in METRICS if not (m.startswith("recall_") and m[7:] in skipped)]
    average_limits = calibration["allowance_average"]
    deployed_limits = calibration["allowance_deployed"]
    deployed_key = ("candidate", DEPLOYMENT_SEED)

    # Check 3: the paired mean over the decision seeds.
    average = {
        m: math.fsum(
            metrics[("candidate", seed)][m] - metrics[("baseline", seed)][m]
            for seed in DECISION_SEEDS
        )
        / len(DECISION_SEEDS)
        for m in METRICS
    }
    reasons += [
        f"average_regressed_{m}" for m in checked if not _passes(average[m], average_limits[m])
    ]

    # Checks 4 and 5: the deployed fit against the fresh baseline and the live model.
    deployed = {
        m: metrics[deployed_key][m] - metrics[("baseline", DEPLOYMENT_SEED)][m] for m in METRICS
    }
    reasons += [
        f"deployed_regressed_{m}" for m in checked if not _passes(deployed[m], deployed_limits[m])
    ]
    live = None
    if live_exists:
        live = {m: metrics[deployed_key][m] - metrics[LIVE][m] for m in METRICS}
        reasons += [
            f"live_regressed_{m}" for m in checked if not _passes(live[m], deployed_limits[m])
        ]
    else:
        reasons.append("no_live_reference")

    # Check 6: every U2R row a reference catches must still be caught by the deployed fit.
    u2r_rows = np.flatnonzero(truth == "u2r")
    candidate = np.asarray(predictions[deployed_key].labels)
    references = [("baseline", ("baseline", DEPLOYMENT_SEED))]
    if live_exists:
        references.append(("live", LIVE))
    caught_any = False
    lost_rows = {}
    for name, key in references:
        reference = np.asarray(predictions[key].labels)
        caught = u2r_rows[reference[u2r_rows] == "u2r"]
        caught_any = caught_any or bool(caught.size)
        lost = caught[candidate[caught] != "u2r"]
        if lost.size:
            reasons.append(f"u2r_detection_lost_{name}")
            lost_rows[name] = [int(index) for index in lost]
    if not caught_any:
        reasons.append("u2r_no_reference_detections")

    rejected = any(
        reason.startswith(
            ("average_regressed_", "deployed_regressed_", "live_regressed_", "u2r_detection_lost_")
        )
        for reason in reasons
    )
    inconclusive = bool(skipped) or "u2r_no_reference_detections" in reasons
    outcome = "reject" if rejected else "inconclusive" if inconclusive else "pass"
    return Decision(
        outcome,
        tuple(reasons),
        {
            "eval_id": eval_id,
            "calibration_id": calibration["calibration_id"],
            "support": {family: support[family] for family in FAMILIES},
            "skipped": skipped,
            "metrics": {_name(key): values for key, values in metrics.items()},
            "average_change": average,
            "deployed_change": deployed,
            "live_change": live,
            "allowance_average": dict(average_limits),
            "allowance_deployed": dict(deployed_limits),
            "u2r_lost_rows": lost_rows,
        },
    )
