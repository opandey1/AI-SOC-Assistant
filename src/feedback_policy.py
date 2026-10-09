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


@dataclass(frozen=True)
class FeedbackPolicy:
    minimum_reviewers: int = 2
    total_weight_fraction: float = 0.10
    reviewer_weight_fraction: float = 0.05

    def __post_init__(self):
        if (
            isinstance(self.minimum_reviewers, bool)
            or not isinstance(self.minimum_reviewers, int)
            or self.minimum_reviewers < 2
        ):
            raise ValueError("Consensus requires at least two reviewer labels.")
        for value in (self.total_weight_fraction, self.reviewer_weight_fraction):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 < value <= 1
            ):
                raise ValueError("Influence fractions must be finite values in (0, 1].")
        if self.reviewer_weight_fraction > self.total_weight_fraction:
            raise ValueError("Reviewer budget must not exceed the total budget.")


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
        votes = list(reviewers.values())
        targets = {(row["disposition"], row["corrected_class"]) for row in votes}
        target = votes[0]["corrected_class"]
        if len(targets) > 1:
            status = "conflict"
        elif (
            "" in reviewers
            or votes[0]["disposition"] != "false_positive"
            or target not in CORRECTABLE_CLASSES
        ):
            status = "excluded"
        elif len(reviewers) < policy.minimum_reviewers:
            status = "awaiting_consensus"
        else:
            status = "eligible"
        summary[status] += 1
        review_ids = tuple(sorted(row["review_id"] for row in votes))
        labels = tuple(sorted(reviewers))
        decisions.append(
            dict(
                ticket_id=ticket_id,
                status=status,
                review_ids=list(review_ids),
                reviewers=list(labels),
            )
        )
        for label, vote in reviewers.items():
            stats[label]["tickets"] += 1
            stats[label][status] += 1
            for other, peer in reviewers.items():
                if other != label:
                    stats[label]["peer_comparisons"] += 1
                    stats[label]["peer_agreements"] += int(
                        (vote["disposition"], vote["corrected_class"])
                        == (peer["disposition"], peer["corrected_class"])
                    )
        if status == "eligible":
            examples.append(
                FeedbackExample(
                    ticket_id=ticket_id,
                    event_id=votes[0]["event_id"],
                    raw_record=votes[0]["raw_record"],
                    corrected_class=target,
                    review_id=max(review_ids),
                    review_ids=review_ids,
                    reviewers=labels,
                )
            )
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
    base_rows: int,
    requested_weight: float,
    policy: FeedbackPolicy = FeedbackPolicy(),
) -> dict:
    if isinstance(base_rows, bool) or not isinstance(base_rows, int) or base_rows < 1:
        raise ValueError("A positive base training row count is required.")
    if (
        isinstance(requested_weight, bool)
        or not isinstance(requested_weight, (int, float))
        or not math.isfinite(requested_weight)
        or not 0 < requested_weight <= 60
    ):
        raise ValueError("Requested weight must be finite and in (0, 60].")
    if not cohort.examples:
        raise ValueError("No consensus corrections are available.")
    contributions = defaultdict(float)
    for example in cohort.examples:
        for reviewer in example.reviewers:
            contributions[reviewer] += requested_weight / len(example.reviewers)
    total_requested = requested_weight * len(cohort.examples)
    total_cap = base_rows * policy.total_weight_fraction
    reviewer_cap = base_rows * policy.reviewer_weight_fraction
    scale = min(
        1.0,
        total_cap / total_requested,
        *(reviewer_cap / value for value in contributions.values()),
    )
    weights = [requested_weight * scale] * len(cohort.examples)
    return {
        "base_training_weight": base_rows,
        "requested_weight": requested_weight,
        "scale": scale,
        "effective_weight": requested_weight * scale,
        "total_weight": sum(weights),
        "total_cap": total_cap,
        "reviewer_cap": reviewer_cap,
        "reviewer_weights": {key: value * scale for key, value in sorted(contributions.items())},
        "sample_weights": weights,
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
