"""Measure what the G2 feedback weight caps bound, on development data only.

Evidence for the G2 decision (docs/g2_weight_caps.md). Development data D is KDDTrain+
without the E3 hold-out rows listed in docs/evaluation/e3_holdout/holdout_rows.json.
Data boundary: the KDDTrain+ file's bytes are hashed to check it against the manifest,
and the hold-out lines are skipped when the file is parsed, so no hold-out row is
parsed, mapped, split, fitted or scored. KDDTest+ is not opened. Writes only the
--output JSON file.

analyse    Exact arithmetic on family counts, no fitting and no data rows. For each cap
           option, oversampling mode, target family and requested weight: the cohort
           size at which the option first scales the weights, and the corrections'
           share of the target family's final class-weighted mass and of all of it.
           Production sizes add the hold-out's class counts recorded in the manifest.
           Every option is evaluated for a cohort aimed at one family.
influence  Random Forest fits on a fixed stratified split of D (80% fit, 20% check): how
           far predictions on the check rows move as cohort size and per-row weight grow.

The RF multiplies each explicit sample weight by its family's balanced class weight,
n_rows / (5 * family_rows), computed from unweighted row counts. A correction's
class-weighted mass is therefore weight * class_weight(target).

Usage: python -m scripts.measure_weight_caps --part analyse --output results.json
       python -m scripts.measure_weight_caps --part influence --output results.json
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.model_selection import train_test_split

import pandas as pd

from src.feedback_policy import FeedbackPolicy
from src.ingest import (
    LABEL_MAP,
    MODEL_INPUT_COLUMNS,
    NSL_KDD_COLUMNS,
    DatasetPaths,
    NslKddDataset,
    TARGET_COLUMN,
    add_attack_family,
)
from src.preprocess import preprocess_dataset
from src.retrain import classification_metrics
from src.train import train_random_forest

SCHEMA = 1
FAMILIES = ("normal", "dos", "probe", "r2l", "u2r")
HOLDOUT_MANIFEST = Path("docs/evaluation/e3_holdout/holdout_rows.json")
SPLIT_SEED = 20261128
COHORT_SEED = 0
REQUESTED_WEIGHTS = (25.0, 60.0)
COHORT_SIZES = (5, 50, 500, 2000)
FAMILY_FRACTIONS = (0.01, 0.05)
GLOBAL_FRACTION = 0.01  # O4: explicit weight within 1% of base rows, oversampling required
POLICY = FeedbackPolicy()

# Influence runs: (mode, scenario, size, weight, rf_seed). Scenarios name the source
# family and the label the cohort is given; "baseline" adds no rows. The weight
# CAPPED_60 means "requested 60, after today's caps for this fit's base rows".
CAPPED_60 = "status-quo cap of 60"
INFLUENCE_PLAN = (
    [("oversampled", "baseline", 0, 0.0, 42), ("oversampled", "baseline", 0, 0.0, 2001)]
    + [
        ("oversampled", "dos->normal", size, weight, 42)
        for size in (50, 250, 1000)
        for weight in (1.0, 5.0, 25.0, 60.0)
    ]
    + [
        ("oversampled", "normal->normal", size, weight, 42)
        for size in (50, 1000)
        for weight in (25.0, 60.0)
    ]
    + [("oversampled", "normal->u2r", size, 25.0, 42) for size in (5, 20)]
    + [("original", "baseline", 0, 0.0, 42)]
    + [
        ("original", "normal->u2r", size, weight, 42)
        for size, weight in ((5, 1.0), (5, 25.0), (20, 25.0))
    ]
    # The E3 pilot's two largest flip sizes, at the weight today's caps would give them,
    # and the larger one uncapped.
    + [("oversampled", "dos->normal", size, CAPPED_60, 42) for size in (1000, 2500)]
    + [("oversampled", "dos->normal", 2500, 60.0, 42)]
    # Where between 25 and 60 per row the 1,000-row flip starts to tip the forest.
    + [("oversampled", "dos->normal", 1000, weight, 42) for weight in (35.0, 45.0)]
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------- analysis


def oversampled_counts(original: dict[str, int]) -> dict[str, int]:
    """Random oversampling raises every family to the majority family's count."""
    top = max(original.values())
    return {family: top for family in original}


def class_weights(counts: dict[str, int]) -> dict[str, float]:
    """scikit-learn's balanced class weights from unweighted row counts."""
    rows = sum(counts.values())
    return {family: rows / (len(counts) * count) for family, count in counts.items()}


def cohort_mass(base: dict[str, int], target: str, size: int, weight: float) -> dict:
    """Class-weighted masses after adding `size` corrections labelled `target`."""
    counts = dict(base)
    counts[target] += size
    weights = class_weights(counts)
    base_mass = {family: base[family] * weights[family] for family in base}
    corrections = size * weight * weights[target]
    return {
        "class_weight": weights[target],
        "corrections": corrections,
        "family": base_mass[target] + corrections,
        "total": sum(base_mass.values()) + corrections,
    }


def status_quo_scale(base_rows: int, size: int, weight: float) -> float:
    """``weight_plan`` with every correction endorsed by the same two reviewer labels."""
    requested = size * weight
    total_cap = base_rows * POLICY.total_weight_fraction
    reviewer_cap = base_rows * POLICY.reviewer_weight_fraction
    return min(1.0, total_cap / requested, reviewer_cap / (requested / 2))


def class_weighted_total_scale(base: dict[str, int], target: str, size: int, weight: float):
    """Corrections' class-weighted mass at most the total fraction of all mass."""
    mass = cohort_mass(base, target, size, weight)
    others = mass["total"] - mass["corrections"]
    fraction = POLICY.total_weight_fraction
    allowed = fraction / (1 - fraction) * others
    return min(1.0, allowed / mass["corrections"])


def family_share_scale(reference_rows: int, size: int, weight: float, fraction: float) -> float:
    """Corrections at most `fraction` of the target family's final class-weighted mass,
    that is at most fraction / (1 - fraction) of `reference_rows` unit-weight rows
    (5% of the final mass is about 5.26% of the baseline rows). The class weight
    multiplies both sides, so it cancels; the reference row count does not, and with
    oversampling it is the majority family's count rather than the family's own."""
    allowed = fraction / (1 - fraction) * reference_rows
    return min(1.0, allowed / (size * weight))


def global_total_scale(base_rows: int, size: int, weight: float) -> float:
    """O4: today's rule with a GLOBAL_FRACTION total (and half that per reviewer label),
    for one budget shared by every target family."""
    requested = size * weight
    total_cap = base_rows * GLOBAL_FRACTION
    return min(1.0, total_cap / requested, total_cap / 2 / (requested / 2))


def option_scales(mode_counts: dict, original: dict, target: str, size: int, weight: float):
    base_rows = sum(mode_counts.values())
    scales = {
        "status_quo": status_quo_scale(base_rows, size, weight),
        "class_weighted_total": class_weighted_total_scale(mode_counts, target, size, weight),
        f"global_total_{GLOBAL_FRACTION:.0%}": global_total_scale(base_rows, size, weight),
    }
    for fraction in FAMILY_FRACTIONS:
        label = f"{fraction:.0%}"
        scales[f"family_share_{label}"] = family_share_scale(
            mode_counts[target], size, weight, fraction
        )
        scales[f"family_share_original_{label}"] = family_share_scale(
            original[target], size, weight, fraction
        )
    return scales


def first_scaled_size(mode_counts, original, target, weight, option, limit=500_000) -> int | None:
    """Smallest cohort at which `option` scales the weights (binary search; monotone)."""

    def scaled(size):
        return option_scales(mode_counts, original, target, size, weight)[option] < 1.0

    if scaled(1):
        return 1
    if not scaled(limit):
        return None
    low, high = 1, limit
    while high - low > 1:
        middle = (low + high) // 2
        low, high = (low, middle) if scaled(middle) else (middle, high)
    return high


def analyse(original: dict[str, int]) -> dict:
    modes = {"oversampled": oversampled_counts(original), "original": dict(original)}
    names = list(option_scales(modes["original"], original, "normal", 1, 1.0))
    result = {"base_rows": {mode: sum(counts.values()) for mode, counts in modes.items()}}
    result["family_rows"] = modes
    result["options"] = {}
    for option in names:
        table = result["options"][option] = {}
        for mode, counts in modes.items():
            table[mode] = {}
            for target in FAMILIES:
                table[mode][target] = {}
                for weight in REQUESTED_WEIGHTS:
                    sizes = {}
                    for size in COHORT_SIZES:
                        scale = option_scales(counts, original, target, size, weight)[option]
                        mass = cohort_mass(counts, target, size, weight * scale)
                        sizes[str(size)] = {
                            "scale": scale,
                            "effective_weight": weight * scale,
                            "class_weighted_row": weight * scale * mass["class_weight"],
                            "family_share": mass["corrections"] / mass["family"],
                            "total_share": mass["corrections"] / mass["total"],
                        }
                    table[mode][target][str(int(weight))] = {
                        "first_scaled_at": first_scaled_size(
                            counts, original, target, weight, option
                        ),
                        "sizes": sizes,
                    }
    return result


# --------------------------------------------------------------------------- influence


def load_development(train_path: Path, manifest_path: Path = HOLDOUT_MANIFEST):
    """D, with every hold-out line skipped while parsing. Only the file's bytes are read
    in full, to check its digest against the manifest."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha256(train_path) != manifest["source_sha256"]:
        raise SystemExit("KDDTrain+ does not match the hold-out manifest's source digest.")
    held_out = frozenset(manifest["indices"])  # 0-based lines; the file has no header
    frame = pd.read_csv(train_path, names=NSL_KDD_COLUMNS, skiprows=lambda line: line in held_out)
    return add_attack_family(frame), manifest


def development_split(train_path: Path) -> tuple[NslKddDataset, dict]:
    development, manifest = load_development(train_path)
    fit, check = train_test_split(
        development,
        test_size=0.2,
        random_state=SPLIT_SEED,
        stratify=development[TARGET_COLUMN],
    )
    split = NslKddDataset(
        train=fit.reset_index(drop=True),
        test=check.reset_index(drop=True),
        paths=DatasetPaths(train=train_path, test=None),
    )
    info = {
        "train_sha256": manifest["source_sha256"],
        "holdout_manifest_sha256": _sha256(HOLDOUT_MANIFEST),
        "development_rows": len(development),
        "fit_rows": len(split.train),
        "check_rows": len(split.test),
        "fit_counts": split.train[TARGET_COLUMN].value_counts().to_dict(),
        "check_counts": split.test[TARGET_COLUMN].value_counts().to_dict(),
        "split_seed": SPLIT_SEED,
    }
    return split, info


def cohort_rows(split: NslKddDataset, source: str, size: int) -> np.ndarray:
    """Positions of `size` fit rows of `source`, in seeded order, skipping any whose
    41 inputs equal a check row."""
    check_inputs = set(map(tuple, split.test[MODEL_INPUT_COLUMNS].itertuples(index=False)))
    eligible = np.flatnonzero(split.train[TARGET_COLUMN].to_numpy() == source)
    order = np.random.default_rng(COHORT_SEED).permutation(eligible)
    inputs = split.train[MODEL_INPUT_COLUMNS]
    chosen = [position for position in order if tuple(inputs.iloc[position]) not in check_inputs][
        :size
    ]
    if len(chosen) < size:
        raise SystemExit(f"Only {len(chosen)} eligible {source} rows for a cohort of {size}.")
    return np.asarray(chosen)


def fit_and_score(data, split, scenario: str, size: int, weight: float, seed: int) -> dict:
    x, y = data.x_train_balanced, data.y_train_balanced
    sample_weight = None
    if scenario != "baseline":
        source, target = scenario.split("->")
        rows = cohort_rows(split, source, size)
        x = np.vstack([x, data.x_train_scaled[rows]])
        y = np.concatenate([y, np.full(size, LABEL_MAP[target], dtype=int)])
        sample_weight = np.concatenate([np.ones(len(data.x_train_balanced)), np.full(size, weight)])
    model = train_random_forest(
        replace(data, x_train_balanced=x, y_train_balanced=y),
        random_state=seed,
        sample_weight=sample_weight,
    )
    predictions = model.predict(data.x_test_scaled)
    labels = np.asarray(data.y_test)
    return {"predictions": predictions, "metrics": classification_metrics(labels, predictions)}


def influence(train_path: Path, output: Path, record: dict, limit: int | None) -> None:
    """Fit the planned runs not yet recorded, at most `limit` of them, saving after each.

    Each mode's seed-42 baseline is refitted on every resume as the reference; its
    prediction digest must match the recorded one, or the run stops.
    """
    split, info = development_split(train_path)
    store = record.setdefault("influence", {"split": info, "baselines": {}, "runs": []})
    if store["split"] != info:
        raise SystemExit("The recorded split differs from this run's split.")
    done = {tuple(run["key"]) for run in store["runs"]}
    fitted = 0
    for mode in ("oversampled", "original"):
        pending = [step for step in INFLUENCE_PLAN if step[0] == mode and step not in done]
        if not pending or (limit is not None and fitted >= limit):
            continue
        data = preprocess_dataset(split, use_smote=mode == "oversampled")
        labels = np.asarray(data.y_test)
        reference = fit_and_score(data, split, "baseline", 0, 0.0, 42)
        digest = hashlib.sha256(np.asarray(reference["predictions"], np.int64).tobytes())
        if store["baselines"].setdefault(mode, digest.hexdigest()) != digest.hexdigest():
            raise SystemExit(f"The {mode} baseline's predictions changed since the last run.")
        for step in pending:
            if limit is not None and fitted >= limit:
                break
            _, scenario, size, weight, seed = step
            if weight == CAPPED_60:
                base_rows = len(data.x_train_balanced)
                weight = 60.0 * status_quo_scale(base_rows, size, 60.0)
            is_reference = scenario == "baseline" and seed == 42
            result = (
                reference
                if is_reference
                else fit_and_score(data, split, scenario, size, weight, seed)
            )
            fitted += 0 if is_reference else 1
            predicted, baseline = result["predictions"], reference["predictions"]
            store["runs"].append(
                {
                    "key": list(step),
                    "weight": weight,
                    "metrics": result["metrics"],
                    "changed": int(np.sum(predicted != baseline)),
                    "broke": int(np.sum((baseline == labels) & (predicted != labels))),
                    "fixed": int(np.sum((baseline != labels) & (predicted == labels))),
                    "predicted_u2r": int(np.sum(predicted == LABEL_MAP["u2r"])),
                }
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"key": list(step), "changed": store["runs"][-1]["changed"]}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--part", choices=("analyse", "influence"), required=True)
    parser.add_argument("--train", type=Path, default=Path("data/KDDTrain+.txt"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Fit at most this many new runs.")
    args = parser.parse_args()
    record = (
        json.loads(args.output.read_text(encoding="utf-8"))
        if args.output.exists()
        else {"schema": SCHEMA}
    )
    if record.get("schema") != SCHEMA:
        raise SystemExit(f"{args.output} is not a schema {SCHEMA} record.")
    if args.part == "analyse":
        manifest = json.loads(HOLDOUT_MANIFEST.read_text(encoding="utf-8"))
        development = {f: manifest["development_class_counts"][f] for f in FAMILIES}
        production = {f: development[f] + manifest["class_counts"][f] for f in FAMILIES}
        record["analysis"] = {
            "policy": {
                "total_weight_fraction": POLICY.total_weight_fraction,
                "reviewer_weight_fraction": POLICY.reviewer_weight_fraction,
            },
            "development": analyse(development),
            "production": analyse(production),
        }
    else:
        influence(args.train, args.output, record, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
