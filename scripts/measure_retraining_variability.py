"""Measure how much RF evaluation metrics move between comparable candidate fits.

Evidence for the E3 acceptance-rule decision (docs/e3_acceptance_rule.md). Uses the
production preprocessing, Random Forest settings, metrics and acceptance check from
src/; it trains in memory and writes only the --output JSON file. Not part of the app.

Arms, each compared with the seed-42 baseline that every candidate is checked against:
  seed        same training data, different Random Forest seed
  correction  N correctly labelled training rows of one family appended at a sample
              weight (1 is a near-neutral control; 25 is the default feedback weight)

Every resume and summary validates the whole record first: schema version, unique
experiment keys, and (on resume) the dataset digests, baseline metrics, ordered
baseline prediction digest and material configuration against a fresh refit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from scipy.stats import binomtest

from src.ingest import (
    LABEL_MAP,
    MODEL_INPUT_COLUMNS,
    TARGET_COLUMN,
    load_nsl_kdd,
    resolve_dataset_paths,
)
from src.model_registry import FAMILIES, acceptance_reasons
from src.preprocess import preprocess_dataset, transform_connections
from src.retrain import classification_metrics
from src.train import train_random_forest

BASELINE_SEED = 42
OVERSAMPLING_SEED = 42
SCHEMA = 2
RF_SETTINGS = (
    "n_estimators",
    "max_depth",
    "min_samples_leaf",
    "max_features",
    "bootstrap",
    "criterion",
    "class_weight",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paired_counts(labels: np.ndarray, reference: np.ndarray, candidate: np.ndarray) -> dict:
    """Rows the candidate broke or fixed relative to the reference, overall and per family."""

    reference_ok, candidate_ok = reference == labels, candidate == labels
    broke, fixed = reference_ok & ~candidate_ok, ~reference_ok & candidate_ok
    per_family = {}
    for family in FAMILIES:
        rows = labels == LABEL_MAP[family]
        per_family[family] = {
            "broke": int(np.sum(broke & rows)),
            "fixed": int(np.sum(fixed & rows)),
        }
    discordant = int(broke.sum() + fixed.sum())
    # One-sided exact McNemar: probability of at least this many broken rows if the
    # candidate were no worse than the reference on the discordant rows.
    p_worse = (
        float(binomtest(int(broke.sum()), discordant, 0.5, alternative="greater").pvalue)
        if discordant
        else 1.0
    )
    return {
        "broke": int(broke.sum()),
        "fixed": int(fixed.sum()),
        "p_worse": p_worse,
        "per_family": per_family,
    }


def gate_report(baseline: dict, candidate: dict, rows: int) -> dict:
    """The fields src.model_registry.acceptance_reasons reads from a retraining report."""

    return {
        "baseline_accuracy": baseline["accuracy"],
        "baseline_macro_f1": baseline["macro_f1"],
        "baseline_per_class_recall": baseline["recall"],
        "updated_accuracy": candidate["accuracy"],
        "updated_macro_f1": candidate["macro_f1"],
        "updated_per_class_recall": candidate["recall"],
        "evaluation_support": candidate["support"],
        "evaluation_rows": rows,
    }


def experiment_key(run: dict) -> tuple:
    """Identity of one fit; the same key twice would double-count a run in the summary."""

    return (
        run["arm"],
        run["rf_seed"],
        run.get("sample_seed"),
        run.get("family"),
        run.get("size"),
        run.get("weight"),
    )


def planned_keys(args: argparse.Namespace) -> list[tuple]:
    if args.arm == "seed":
        return [
            ("seed", 1000 + i, None, None, None, None)
            for i in range(args.start, args.start + args.runs)
        ]
    return [
        ("correction", args.rf_seed, i, args.family, args.size, float(args.weight))
        for i in range(args.start, args.start + args.runs)
    ]


def load_record(path: Path, *, allow_legacy: bool = False) -> dict:
    """Strict checks shared by resume and summary; a legacy record needs --migrate."""

    results = json.loads(path.read_text(encoding="utf-8"))
    legacy = "schema" not in results
    if legacy and not allow_legacy:
        raise SystemExit(
            f"{path} predates schema {SCHEMA}; validate and upgrade it with --migrate."
        )
    required = (
        ("dataset", "baseline", "runs") if legacy else ("dataset", "baseline", "config", "runs")
    )
    missing = [name for name in required if name not in results]
    if missing:
        raise SystemExit(f"{path} is missing {missing}.")
    if not legacy:
        if results["schema"] != SCHEMA:
            raise SystemExit(f"{path} uses schema {results['schema']}; expected {SCHEMA}.")
        if "prediction_sha256" not in results["baseline"]:
            raise SystemExit(f"{path} has no baseline prediction digest.")
    keys = [experiment_key(run) for run in results["runs"]]
    duplicates = sorted({key for key in keys if keys.count(key) > 1}, key=str)
    if duplicates:
        raise SystemExit(f"{path} contains duplicate experiment keys: {duplicates}")
    return results


def _save(path: Path, results: dict) -> None:
    # LF on every platform, so a write never changes the committed file's line endings.
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")


def _legacy_config_matches(old: dict | None, new: dict) -> bool:
    """A pre-schema record may hold a subset of today's settings; each must agree."""

    if old is None:
        return True
    if old.get("use_smote") != new["preprocessing"]["use_smote"]:
        return False
    return all(new["random_forest"].get(k) == v for k, v in old.get("random_forest", {}).items())


def run_arm(args: argparse.Namespace) -> None:
    output = Path(args.output)
    validate_only = args.runs == 0 and not args.migrate
    if validate_only and not output.exists():
        raise SystemExit(f"{output} does not exist; --runs 0 only validates an existing record.")
    results = load_record(output, allow_legacy=args.migrate) if output.exists() else None
    if args.migrate and (results is None or "schema" in results):
        raise SystemExit("--migrate only upgrades an existing pre-schema record.")
    existing = {experiment_key(run) for run in results["runs"]} if results else set()
    duplicates = [key for key in planned_keys(args) if key in existing]
    if duplicates:
        raise SystemExit(f"Refusing to append runs already in {output}: {duplicates}")

    paths = resolve_dataset_paths(args.train, args.test, search_roots=[Path.cwd()])
    dataset = load_nsl_kdd(paths.train, paths.test)
    data = preprocess_dataset(dataset, use_smote=True, random_state=OVERSAMPLING_SEED)
    labels = data.y_test.to_numpy()

    baseline_model = train_random_forest(data, random_state=BASELINE_SEED)
    baseline_pred = baseline_model.predict(data.x_test_scaled)
    metrics = classification_metrics(data.y_test, baseline_pred)
    prediction_digest = hashlib.sha256(
        np.ascontiguousarray(baseline_pred, dtype=np.int64).tobytes()
    ).hexdigest()
    params = baseline_model.get_params()
    identity = {
        "dataset": {
            "sha256": {"train": _sha256(paths.train), "test": _sha256(paths.test)},
            "train_rows": int(len(dataset.train)),
            "test_rows": int(len(labels)),
            "balanced_training_rows": int(len(data.x_train_balanced)),
        },
        "baseline": {"rf_seed": BASELINE_SEED, **metrics, "prediction_sha256": prediction_digest},
        "config": {
            "preprocessing": {"use_smote": True, "oversampling_seed": OVERSAMPLING_SEED},
            "random_forest": {name: params[name] for name in RF_SETTINGS},
        },
    }

    if results is None:
        results = {"schema": SCHEMA, **identity, "runs": []}
    elif results["dataset"] != identity["dataset"]:
        raise SystemExit("The output file was produced from different dataset files.")
    elif args.migrate:
        legacy_baseline = {
            k: v for k, v in identity["baseline"].items() if k != "prediction_sha256"
        }
        if results["baseline"] != legacy_baseline:
            raise SystemExit("The legacy baseline metrics do not match this environment's refit.")
        if not _legacy_config_matches(results.get("config"), identity["config"]):
            raise SystemExit("The legacy configuration differs from today's settings.")
        results = {"schema": SCHEMA, **identity, "runs": results["runs"]}
        print(f"Migrated {output} to schema {SCHEMA} after validating it.", flush=True)
    elif results["baseline"] != identity["baseline"] or results["config"] != identity["config"]:
        raise SystemExit("The saved baseline or configuration does not match this refit.")
    if validate_only:
        print(f"{output} matches a fresh refit; nothing was written.", flush=True)
        return
    runs = results["runs"]
    _save(output, results)

    for index in range(args.start, args.start + args.runs):
        if args.arm == "seed":
            model = train_random_forest(data, random_state=1000 + index)
            details = {"rf_seed": 1000 + index}
        else:
            source = dataset.train[dataset.train[TARGET_COLUMN] == args.family]
            rows = source.sample(args.size, random_state=index)
            _, scaled = transform_connections(rows[MODEL_INPUT_COLUMNS], data)
            extra_y = np.full(args.size, LABEL_MAP[args.family], dtype=int)
            updated = replace(
                data,
                x_train_balanced=np.vstack([data.x_train_balanced, scaled]),
                y_train_balanced=np.concatenate([data.y_train_balanced, extra_y]),
            )
            weights = np.concatenate(
                [np.ones(len(data.x_train_balanced)), np.full(args.size, args.weight)]
            )
            model = train_random_forest(updated, random_state=args.rf_seed, sample_weight=weights)
            own = model.predict(scaled)
            details = {
                "rf_seed": args.rf_seed,
                "sample_seed": index,
                "family": args.family,
                "size": args.size,
                "weight": float(args.weight),
                "own_rows_changed": int(np.sum(baseline_model.predict(scaled) != own)),
            }
        prediction = model.predict(data.x_test_scaled)
        candidate = classification_metrics(data.y_test, prediction)
        report = gate_report(metrics, candidate, len(labels))
        runs.append(
            {
                "arm": args.arm,
                "index": index,
                **details,
                "accuracy": candidate["accuracy"],
                "macro_f1": candidate["macro_f1"],
                "recall": candidate["recall"],
                "paired": paired_counts(labels, baseline_pred, prediction),
                "zero_tolerance_reasons": acceptance_reasons(report),
            }
        )
        _save(output, results)
        print(
            f"{args.arm} #{index}: accuracy {candidate['accuracy']:.4f} "
            f"reasons={runs[-1]['zero_tolerance_reasons']}",
            flush=True,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--train", type=Path, help="Path to KDDTrain+.txt.")
    parser.add_argument("--test", type=Path, help="Path to KDDTest+.txt.")
    parser.add_argument("--arm", choices=["seed", "correction"])
    parser.add_argument(
        "--runs", type=int, default=8, help="Fits to add; 0 only validates the saved record."
    )
    parser.add_argument("--start", type=int, default=0, help="First run index (to resume).")
    parser.add_argument("--family", default="normal", choices=FAMILIES)
    parser.add_argument("--size", type=int, default=5, help="Correction rows per candidate.")
    parser.add_argument("--weight", type=float, default=25.0)
    parser.add_argument(
        "--rf-seed",
        type=int,
        default=BASELINE_SEED,
        help="Random Forest seed for correction runs; compare with the seed arm's run of the same seed.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Validate a pre-schema record against a fresh refit and upgrade it.",
    )
    parser.add_argument(
        "--summarize", action="store_true", help="Print tables from --output and exit."
    )
    return parser


def _pp(value: float) -> str:
    return f"{100 * value:+.2f}"


def summarize(path: Path) -> None:
    """Markdown tables for docs/e3_acceptance_rule.md, recomputed from the raw runs."""

    results = load_record(path)
    baseline, rows = results["baseline"], results["dataset"]["test_rows"]
    seeds = {run["rf_seed"]: run for run in results["runs"] if run["arm"] == "seed"}
    groups: dict[tuple, list[dict]] = {}
    for run in results["runs"]:
        if run["arm"] == "seed":
            groups.setdefault(("seed",), []).append(run)
        elif run["rf_seed"] == BASELINE_SEED:
            # Key on exact values so distinct weights never pool; repr keeps labels distinct.
            groups.setdefault((run["family"], run["size"], run["weight"]), []).append(run)

    def label(key: tuple) -> str:
        if key == ("seed",):
            return "Seed only"
        family, size, weight = key
        return f"Correction, {size} {family} rows, weight {weight!r}"

    def drop(run: dict, reference: dict, metric: str) -> float:
        if metric in ("accuracy", "macro_f1"):
            return run[metric] - reference[metric]
        return run["recall"][metric] - reference["recall"][metric]

    metrics = ("accuracy", "macro_f1", *FAMILIES)
    seed_runs = groups.get(("seed",), [])
    bands = (
        {m: min(0.0, *(drop(r, baseline, m) for r in seed_runs)) for m in metrics}
        if seed_runs
        else None
    )

    print("| Arm (vs seed-42 baseline) | Runs | " + " | ".join(metrics) + " |")
    print("|---|---:|" + "---:|" * len(metrics))
    for key, runs in groups.items():
        cells = []
        for m in metrics:
            values = [drop(r, baseline, m) for r in runs]
            cells.append(f"{_pp(min(values))} to {_pp(max(values))}")
        print(f"| {label(key)} | {len(runs)} | " + " | ".join(cells) + " |")

    print(
        "\n| Arm | Own predictions changed | Current rule | Not flagged by McNemar | Seed-range bands |"
    )
    print("|---|---:|---:|---:|---:|")
    for key, runs in groups.items():
        own = "-" if key == ("seed",) else str(sum(r["own_rows_changed"] for r in runs))
        zero = sum(not r["zero_tolerance_reasons"] for r in runs)
        mcnemar = sum(r["paired"]["p_worse"] >= 0.05 for r in runs)
        banded = (
            f"{sum(all(drop(r, baseline, m) >= bands[m] - 1e-12 for m in metrics) for r in runs)}/{len(runs)} pass"
            if bands
            else "n/a"
        )
        print(
            f"| {label(key)} | {own} | {zero}/{len(runs)} pass | {mcnemar}/{len(runs)} | {banded} |"
        )
    if bands:
        print(
            "\nSeed-range bands (largest drop in the seed arm): "
            + ", ".join(f"{m} {_pp(v)}" for m, v in bands.items())
        )

    matched = [r for r in results["runs"] if r["arm"] == "correction" and r["rf_seed"] in seeds]
    if matched:
        print(
            "\n| Cohort | Rows | Weight | RF seed | accuracy | macro_f1 | dos | r2l | Current rule vs same seed |"
        )
        print("|---:|---|---:|---:|---:|---:|---:|---:|---|")
        for run in sorted(
            matched,
            key=lambda r: (r["family"], r["size"], r["weight"], r["sample_seed"], r["rf_seed"]),
        ):
            reference = seeds[run["rf_seed"]]
            report = gate_report(reference, {**run, "support": baseline["support"]}, rows)
            reasons = acceptance_reasons(report)
            print(
                f"| {run['sample_seed']} | {run['size']} {run['family']} | {run['weight']!r} | {run['rf_seed']} | "
                + " | ".join(
                    _pp(drop(run, reference, m)) for m in ("accuracy", "macro_f1", "dos", "r2l")
                )
                + f" | {'pass' if not reasons else ', '.join(reasons)} |"
            )


if __name__ == "__main__":
    parser = build_parser()
    arguments = parser.parse_args()
    if arguments.summarize:
        summarize(arguments.output)
    elif arguments.arm is None:
        parser.error("--arm is required unless --summarize is given")
    else:
        run_arm(arguments)
