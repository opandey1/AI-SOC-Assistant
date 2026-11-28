"""Consensus and sample-weight budgets for a trusted local feedback prototype."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import unicodedata

from src.feedback import CORRECTABLE_CLASSES, FeedbackExample, FeedbackStore

# Version of the governance record in training reports; 2 is the per-family budget.
FEEDBACK_GOVERNANCE_VERSION = 2


def is_finite_number(value) -> bool:
    """A real number (not a bool) that converts to a finite float.

    JSON integers are unbounded, so one beyond the float range counts as invalid input
    rather than raising ``OverflowError``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_fraction(value) -> bool:
    return is_finite_number(value) and 0 < value <= 1


def check_budget_inputs(family_rows, requested_weight) -> None:
    """Raise ``ValueError`` unless these are valid ``weight_plan`` inputs: a count of
    training rows for each of the five families, and a requested weight in (0, 60]."""
    if (
        not isinstance(family_rows, dict)
        or set(family_rows) != set(CORRECTABLE_CLASSES)
        or any(
            not isinstance(count, int) or not is_finite_number(count) or count < 0
            for count in family_rows.values()
        )
    ):
        raise ValueError("Baseline training rows are required for each of the five families.")
    if not is_finite_number(requested_weight) or not 0 < requested_weight <= 60:
        raise ValueError("Requested weight must be finite and in (0, 60].")


@dataclass(frozen=True)
class FeedbackPolicy:
    """Consensus and per-family influence budgets (ADR-002, option O2).

    The corrections aimed at one family may carry at most ``family_mass_fraction`` of
    that family's final class-weighted training mass. One reviewer label may be
    credited with at most ``reviewer_budget_fraction`` of each family's budget.
    """

    minimum_reviewers: int = 2
    family_mass_fraction: float = 0.05
    reviewer_budget_fraction: float = 0.5

    def __post_init__(self):
        if (
            isinstance(self.minimum_reviewers, bool)
            or not isinstance(self.minimum_reviewers, int)
            or self.minimum_reviewers < 2
        ):
            raise ValueError("Consensus requires at least two reviewer labels.")
        if not _is_fraction(self.family_mass_fraction) or self.family_mass_fraction == 1:
            raise ValueError("The family mass fraction must be a finite value in (0, 1).")
        if not _is_fraction(self.reviewer_budget_fraction):
            raise ValueError("The reviewer budget fraction must be a finite value in (0, 1].")


def reviewer_key(label: str) -> str:
    """Collapse accidental name variants, not aliases or forged identities."""
    return " ".join(unicodedata.normalize("NFKC", label).casefold().split())


@dataclass(frozen=True)
class FeedbackCohort:
    examples: tuple[FeedbackExample, ...]
    summary: dict
    decisions: tuple[dict, ...]
    reviewers: tuple[dict, ...]
    snapshot_sha256: str

    def audit(self) -> dict:
        return {
            "summary": self.summary,
            "decisions": list(self.decisions),
            "reviewers": list(self.reviewers),
            "snapshot_sha256": self.snapshot_sha256,
        }


def build_cohort(rows: list[dict], policy: FeedbackPolicy = FeedbackPolicy()) -> FeedbackCohort:
    latest = defaultdict(dict)
    for row in sorted(rows, key=lambda item: item["review_id"]):
        latest[row["ticket_id"]][reviewer_key(row["reviewed_by"])] = row
    examples, decisions, snapshot = [], [], []
    stats = defaultdict(
        lambda: dict(
            tickets=0,
            eligible=0,
            conflict=0,
            awaiting_consensus=0,
            excluded=0,
            peer_agreements=0,
            peer_comparisons=0,
        )
    )
    summary = dict(eligible=0, conflict=0, awaiting_consensus=0, excluded=0)
    for ticket_id, reviewers in sorted(latest.items()):
        votes = {
            label: (
                row["disposition"],
                row["corrected_class"] if row["disposition"] == "false_positive" else None,
            )
            for label, row in reviewers.items()
        }
        disposition, target = next(iter(votes.values()))
        if len(set(votes.values())) > 1:
            status = "conflict"
        elif (
            "" in reviewers or disposition != "false_positive" or target not in CORRECTABLE_CLASSES
        ):
            status = "excluded"
        elif len(reviewers) < policy.minimum_reviewers:
            status = "awaiting_consensus"
        else:
            status = "eligible"
        summary[status] += 1
        review_ids = tuple(sorted(row["review_id"] for row in reviewers.values()))
        labels = tuple(sorted(reviewers))
        decisions.append(
            dict(
                ticket_id=ticket_id,
                status=status,
                review_ids=list(review_ids),
                reviewers=list(labels),
            )
        )
        for label, vote in votes.items():
            stats[label]["tickets"] += 1
            stats[label][status] += 1
            for other, peer in votes.items():
                if other != label:
                    stats[label]["peer_comparisons"] += 1
                    stats[label]["peer_agreements"] += int(vote == peer)
        if status == "eligible":
            first_review = next(iter(reviewers.values()))
            examples.append(
                FeedbackExample(
                    ticket_id=ticket_id,
                    event_id=first_review["event_id"],
                    raw_record=first_review["raw_record"],
                    corrected_class=target,
                    review_id=max(review_ids),
                    review_ids=review_ids,
                    reviewers=labels,
                )
            )
        # Preserve raw review fields in the fingerprint used for promotion freshness.
        snapshot.extend({**row, "reviewed_by": label} for label, row in sorted(reviewers.items()))
    for label, counts in sorted(stats.items()):
        comparisons = counts["peer_comparisons"]
        counts["reviewer"] = label
        counts["peer_agreement_rate"] = (
            counts["peer_agreements"] / comparisons if comparisons else None
        )
    digest = sha256(
        json.dumps(
            {"policy": asdict(policy), "latest_reviews": snapshot}, sort_keys=True, allow_nan=False
        ).encode()
    ).hexdigest()
    return FeedbackCohort(
        tuple(examples),
        summary,
        tuple(decisions),
        tuple(stats[key] for key in sorted(stats)),
        digest,
    )


def weight_plan(
    cohort: FeedbackCohort,
    *,
    family_rows: dict[str, int],
    requested_weight: float,
    policy: FeedbackPolicy = FeedbackPolicy(),
) -> dict:
    """Scale each target family's corrections to that family's own budget.

    ``family_rows`` are each family's training rows before corrections are added, after
    random oversampling. RF ``class_weight="balanced"`` gives a family's baseline rows
    and its corrections the same class weight, so capping the corrections at f of the
    family's final class-weighted mass caps their explicit weight at f / (1 - f) of its
    baseline rows. Each endorsing label is credited an equal share of a correction's
    weight; with at least two labels per correction, a reviewer budget of half the
    family budget cannot bind before the family budget does.
    """
    check_budget_inputs(family_rows, requested_weight)
    if not cohort.examples:
        raise ValueError("No consensus corrections are available.")
    if any(
        example.corrected_class not in CORRECTABLE_CLASSES
        or len(set(example.reviewers)) < policy.minimum_reviewers
        for example in cohort.examples
    ):
        raise ValueError("Every correction needs a valid family and the required reviewer labels.")
    share = policy.family_mass_fraction / (1 - policy.family_mass_fraction)
    families = {}
    for family in CORRECTABLE_CLASSES:
        examples = [example for example in cohort.examples if example.corrected_class == family]
        if not examples:
            continue
        if not family_rows[family]:
            raise ValueError(f"Corrections target {family}, which has no baseline training rows.")
        contributions = defaultdict(float)
        for example in examples:
            for reviewer in example.reviewers:
                contributions[reviewer] += requested_weight / len(example.reviewers)
        requested = requested_weight * len(examples)
        budget = share * family_rows[family]
        reviewer_budget = budget * policy.reviewer_budget_fraction
        scale = min(
            1.0,
            budget / requested,
            *(reviewer_budget / value for value in contributions.values()),
        )
        families[family] = {
            "corrections": len(examples),
            "requested_total": requested,
            "budget": budget,
            "reviewer_budget": reviewer_budget,
            "scale": scale,
            "effective_weight": requested_weight * scale,
            "total_weight": requested * scale,
            "reviewer_weights": {
                key: value * scale for key, value in sorted(contributions.items())
            },
        }
    return {
        "requested_weight": requested_weight,
        "family_rows": {family: family_rows[family] for family in CORRECTABLE_CLASSES},
        "families": families,
        "total_weight": sum(plan["total_weight"] for plan in families.values()),
        "sample_weights": [
            families[example.corrected_class]["effective_weight"] for example in cohort.examples
        ],
    }


@contextmanager
def locked_cohort(database_path: str | Path, policy: FeedbackPolicy):
    """Hold a review write lock through promotion; never recreate a missing database."""
    path = Path(database_path).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield build_cohort(FeedbackStore._review_rows(connection), policy)
    finally:
        connection.rollback()
        connection.close()
