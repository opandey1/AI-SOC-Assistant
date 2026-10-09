"""Local candidate bundles and transactional model selection, not an authenticated registry."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4

from src.retrain import (
    MINIMUM_FEEDBACK_EXAMPLES,
    classification_metrics,
    file_digest,
    retrain_from_feedback,
)
from src.feedback_policy import FeedbackPolicy, locked_cohort, weight_plan

FAMILIES = ("normal", "dos", "probe", "r2l", "u2r")


def acceptance_reasons(report: dict, reference: dict | None = None) -> list[str]:
    """Zero-tolerance RF non-regression on the supplied evaluation file only."""
    reasons = []
    reference = reference or {
        "accuracy": report["baseline_accuracy"],
        "macro_f1": report["baseline_macro_f1"],
        "recall": report["baseline_per_class_recall"],
    }
    for metric in ("accuracy", "macro_f1"):
        before, after = reference[metric], report[f"updated_{metric}"]
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 1
            for value in (before, after)
        ):
            reasons.append(f"invalid_{metric}")
        elif after + 1e-12 < before:
            reasons.append(f"regressed_{metric}")
    support = report["evaluation_support"]
    if report["evaluation_rows"] <= 0 or sum(support.values()) != report["evaluation_rows"]:
        reasons.append("invalid_evaluation_population")
    for family in FAMILIES:
        if support.get(family, 0) <= 0:
            reasons.append(f"missing_evaluation_class_{family}")
        before = reference["recall"].get(family)
        after = report["updated_per_class_recall"].get(family)
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 1
            for value in (before, after)
        ):
            reasons.append(f"invalid_recall_{family}")
        elif after + 1e-12 < before:
            reasons.append(f"regressed_recall_{family}")
    return reasons


class ModelRegistry:
    """Immutable-by-convention bundles with a SQLite active/previous pointer.

    Reads never create a registry. SQLite serializes pointer changes and their audit
    entries; generation checks reject stale browser/CLI actions. Filesystem owners
    remain trusted, especially for joblib's executable pickle format.
    """

    def __init__(self, root: str | Path, *, legacy_model: str | Path | None = None):
        self.root = Path(root).resolve()
        self.database = self.root / "registry.sqlite"
        self.legacy_model = Path(legacy_model).resolve() if legacy_model else None

    @contextmanager
    def _connect(self, *, initialize=False):
        if initialize:
            self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        if initialize:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, manifest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS selection (
                    id INTEGER PRIMARY KEY CHECK(id = 1), active TEXT, previous TEXT,
                    generation INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO selection VALUES (1, NULL, NULL, 0);
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL,
                    actor TEXT NOT NULL, old_model TEXT, new_model TEXT, generation INTEGER NOT NULL
                );
            """
            )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def state(self) -> dict:
        if not self.database.exists():
            return dict(active=None, previous=None, generation=0)
        with self._connect() as connection:
            return dict(
                connection.execute(
                    "SELECT active, previous, generation FROM selection WHERE id = 1"
                ).fetchone()
            )

    def candidate(self, candidate_id: str, *, connection=None) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", candidate_id):
            raise ValueError("Invalid candidate ID.")
        if connection is None:
            with self._connect() as owned:
                return self.candidate(candidate_id, connection=owned)
        row = connection.execute(
            "SELECT manifest FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Candidate does not exist in this registry.")
        manifest = json.loads(row["manifest"])
        directory = (self.root / candidate_id).resolve()
        if directory.parent != self.root:
            raise ValueError("Candidate path escapes the registry.")
        for name in ("model.joblib", "report.json"):
            path = directory / name
            if path.resolve().parent != directory or file_digest(path) != manifest["sha256"][name]:
                raise ValueError("Candidate bundle integrity check failed.")
        return manifest

    def active_path(self) -> Path | None:
        state = self.state()
        active = state["active"]
        if active:
            self.candidate(active)
            return self.root / active / "model.joblib"
        if state["generation"] == 0 and self.legacy_model and self.legacy_model.exists():
            return self.legacy_model
        return None

    def candidates(self) -> list[dict]:
        if not self.database.exists():
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT manifest FROM candidates ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [json.loads(row["manifest"]) for row in rows]

    def history(self) -> list[dict]:
        if not self.database.exists():
            return []
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM actions ORDER BY id")]

    def _register(self, manifest: dict, connection):
        connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?)",
            (manifest["id"], manifest["created_at"], json.dumps(manifest, allow_nan=False)),
        )

    def _reference_hash(self) -> str | None:
        path = self.active_path()
        return file_digest(path) if path else None

    def create_candidate(
        self,
        *,
        database_path,
        train_path=None,
        test_path=None,
        feedback_weight=25.0,
        use_smote=True,
        isolation_threshold=0.7,
    ) -> dict:
        """Train in an owned staging directory; publish only a complete bundle."""
        from src.ingest import LABEL_MAP, MODEL_INPUT_COLUMNS, TARGET_COLUMN, load_nsl_kdd
        from src.model_store import load_model_artifact
        from src.preprocess import transform_connections

        state = self.state()
        reference_path = self.active_path()
        reference_hash = self._reference_hash()
        candidate_id = uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        stage = self.root / f".stage-{candidate_id}"
        destination = self.root / candidate_id
        stage.mkdir()
        try:
            report = retrain_from_feedback(
                database_path=database_path,
                output_model=stage / "model.joblib",
                train_path=train_path,
                test_path=test_path,
                feedback_weight=feedback_weight,
                use_smote=use_smote,
                isolation_threshold=isolation_threshold,
                feedback_policy=FeedbackPolicy(),
            )
            payload = asdict(report)
            # The path in a published report must not point at a removed staging directory.
            payload["output_model"] = str(destination / "model.joblib")
            reference_metrics = None
            if reference_path:
                artifact = load_model_artifact(reference_path)
                dataset = load_nsl_kdd(train_path, test_path)
                if file_digest(dataset.paths.test) != report.provenance["datasets"]["test"]:
                    raise ValueError("Evaluation dataset changed during candidate comparison.")
                _, scaled = transform_connections(dataset.test[MODEL_INPUT_COLUMNS], artifact)
                labels = dataset.test[TARGET_COLUMN].map(LABEL_MAP).to_numpy()
                reference_metrics = classification_metrics(
                    labels, artifact.random_forest.predict(scaled)
                )
            reasons = acceptance_reasons(payload)
            if reference_metrics:
                reasons.extend(
                    f"active_{reason}" for reason in acceptance_reasons(payload, reference_metrics)
                )
            if self.state() != state or self._reference_hash() != reference_hash:
                raise ValueError(
                    "Active model changed during candidate training; retry with a fresh reference."
                )
            (stage / "report.json").write_text(
                json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
            )
            manifest = {
                "id": candidate_id,
                "created_at": report.trained_at,
                "model_version": report.model_version,
                "kind": "feedback",
                "feedback_database": str(Path(database_path).resolve()),
                "parent_generation": state["generation"],
                "reference_sha256": reference_hash,
                "accepted": not reasons,
                "reasons": reasons,
                "active_reference_metrics": reference_metrics,
                "sha256": {
                    name: file_digest(stage / name) for name in ("model.joblib", "report.json")
                },
            }
            (stage / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            os.rename(stage, destination)
            with self._connect(initialize=True) as connection:
                self._register(manifest, connection)
            return manifest
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            # A completed but unregistered directory is inert and retained for diagnostics.
            raise

    def report(self, candidate_id: str) -> dict:
        self.candidate(candidate_id)
        return json.loads((self.root / candidate_id / "report.json").read_text(encoding="utf-8"))

    def _change(self, *, candidate_id=None, expected_generation: int, actor: str, rollback=False):
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise ValueError("expected_generation must be a nonnegative integer.")
        if not actor.strip():
            raise ValueError("An operator name is required (not authenticated identity).")
        with ExitStack() as guards, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = dict(
                connection.execute(
                    "SELECT active, previous, generation FROM selection WHERE id = 1"
                ).fetchone()
            )
            if state["generation"] != expected_generation:
                raise ValueError("Stale model selection; refresh before changing the active model.")
            old = state["active"]
            if rollback:
                if state["generation"] == 0:
                    raise ValueError("No previous model selection is available.")
                target = state["previous"]
                if target:
                    self.candidate(target, connection=connection)
            else:
                manifest = self.candidate(candidate_id, connection=connection)
                if manifest["kind"] != "feedback":
                    raise ValueError(
                        "Legacy snapshots are rollback targets, not promotable candidates."
                    )
                report = json.loads(
                    (self.root / candidate_id / "report.json").read_text(encoding="utf-8")
                )
                governance = report.get("feedback_governance", {})
                policy = FeedbackPolicy()
                if (
                    governance.get("version") != 1
                    or governance.get("policy") != asdict(policy)
                    or not manifest.get("feedback_database")
                ):
                    raise ValueError(
                        "Candidate predates the current feedback policy; train a new candidate."
                    )
                cohort = guards.enter_context(locked_cohort(manifest["feedback_database"], policy))
                if cohort.snapshot_sha256 != governance["cohort"]["snapshot_sha256"]:
                    raise ValueError("Review decisions changed; train a new candidate.")
                weights = governance["weights"]
                if len(cohort.examples) < MINIMUM_FEEDBACK_EXAMPLES or weights != weight_plan(
                    cohort,
                    base_rows=weights["base_training_weight"],
                    requested_weight=report["feedback_weight"],
                    policy=policy,
                ):
                    raise ValueError("Candidate feedback budget or cohort is invalid.")
                reasons = acceptance_reasons(report)
                reference = manifest["active_reference_metrics"]
                if reference:
                    reasons.extend(acceptance_reasons(report, reference))
                if not manifest["accepted"] or reasons:
                    raise ValueError("Candidate failed the evaluation non-regression check.")
                if manifest["parent_generation"] != state["generation"]:
                    raise ValueError("Candidate reference is stale; train a new candidate.")
                current = (
                    (self.root / old / "model.joblib")
                    if old
                    else self.legacy_model if state["generation"] == 0 else None
                )
                digest = file_digest(current) if current and current.exists() else None
                if digest != manifest["reference_sha256"]:
                    raise ValueError("Active reference integrity changed; train a new candidate.")
                if not old and digest:
                    # Preserve the pre-registry artifact so first-promotion rollback is real.
                    old = uuid4().hex
                    directory = self.root / old
                    directory.mkdir()
                    shutil.copyfile(current, directory / "model.joblib")
                    (directory / "report.json").write_text("{}\n", encoding="utf-8")
                    if file_digest(directory / "model.joblib") != digest:
                        raise ValueError("Legacy model changed during snapshot.")
                    legacy = {
                        "id": old,
                        "kind": "legacy",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "model_version": "legacy-import",
                        "accepted": False,
                        "sha256": {
                            name: file_digest(directory / name)
                            for name in ("model.joblib", "report.json")
                        },
                    }
                    self._register(legacy, connection)
                target = candidate_id
            generation = state["generation"] + 1
            connection.execute(
                "UPDATE selection SET active = ?, previous = ?, generation = ? WHERE id = 1",
                (target, old, generation),
            )
            connection.execute(
                "INSERT INTO actions (created_at, action, actor, old_model, new_model, generation) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    "rollback" if rollback else "promote",
                    actor.strip(),
                    old,
                    target,
                    generation,
                ),
            )
        return self.state()

    def promote(self, candidate_id: str, *, expected_generation: int, actor: str) -> dict:
        return self._change(
            candidate_id=candidate_id, expected_generation=expected_generation, actor=actor
        )

    def rollback(self, *, expected_generation: int, actor: str) -> dict:
        return self._change(expected_generation=expected_generation, actor=actor, rollback=True)


def main():
    parser = argparse.ArgumentParser(
        description="Inspect, promote or roll back a local SOC candidate."
    )
    parser.add_argument("--registry", type=Path, default=Path("models/registry"))
    parser.add_argument("--legacy-model", type=Path, default=Path("models/soc_model.joblib"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for action in ("promote", "rollback"):
        command = commands.add_parser(action)
        if action == "promote":
            command.add_argument("candidate_id")
        command.add_argument("--expected-generation", type=int, required=True)
        command.add_argument("--operator", required=True)
    args = parser.parse_args()
    registry = ModelRegistry(args.registry, legacy_model=args.legacy_model)
    if args.command == "list":
        result = {
            "selection": registry.state(),
            "candidates": registry.candidates(),
            "history": registry.history(),
        }
    elif args.command == "promote":
        result = registry.promote(
            args.candidate_id, expected_generation=args.expected_generation, actor=args.operator
        )
    else:
        result = registry.rollback(
            expected_generation=args.expected_generation, actor=args.operator
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
