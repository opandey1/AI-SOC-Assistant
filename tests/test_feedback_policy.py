"""Local consensus, bias diagnostics and actual influence budgets, not identity assurance."""

from dataclasses import asdict
import sqlite3

import pytest

from src.feedback_policy import (
    FeedbackPolicy,
    build_cohort,
    locked_cohort,
    reviewer_key,
    weight_plan,
)


def votes(ticket=1, reviewers=("Alice", "Bob"), target="normal", first_id=1):
    return [
        dict(
            ticket_id=ticket,
            event_id=f"event-{ticket}",
            raw_record={"duration": ticket},
            review_id=first_id + i,
            reviewed_by=name,
            disposition="false_positive",
            corrected_class=target,
        )
        for i, name in enumerate(reviewers)
    ]


def test_single_reviewer_cannot_supply_quorum_by_repeated_reviews():
    rows = votes(reviewers=("Alice", " alice ", "ALICE", "Ａｌｉｃｅ"))
    cohort = build_cohort(rows)
    assert reviewer_key("  ALICE  Smith ") == "alice smith"
    assert not cohort.examples
    assert cohort.summary == dict(eligible=0, conflict=0, awaiting_consensus=1, excluded=0)
    assert cohort.decisions[0]["review_ids"] == [4]
    assert cohort.reviewers[0]["peer_agreement_rate"] is None


def test_two_agreeing_latest_decisions_preserve_exact_lineage():
    rows = votes()
    cohort = build_cohort(rows)
    example = cohort.examples[0]
    assert example.reviewers == ("alice", "bob") and example.review_ids == (1, 2)
    assert example.review_id == 2 and example.corrected_class == "normal"
    assert cohort.summary["eligible"] == 1
    assert all(row["peer_agreement_rate"] == 1 for row in cohort.reviewers)
    assert build_cohort(list(reversed(rows))).snapshot_sha256 == cohort.snapshot_sha256
    assert "raw_record" not in str(cohort.audit())


@pytest.mark.parametrize(
    "disposition,target",
    [
        ("false_positive", "dos"),
        ("confirmed_attack", None),
        ("needs_investigation", None),
    ],
)
def test_conflicts_are_held_out_even_with_majority_support(disposition, target):
    rows = votes(reviewers=("alice", "bob", "carol"))
    rows[-1].update(disposition=disposition, corrected_class=target)
    cohort = build_cohort(rows)
    assert not cohort.examples and cohort.summary["conflict"] == 1
    assert cohort.reviewers[0]["peer_agreement_rate"] == 0.5
    assert cohort.reviewers[-1]["peer_agreement_rate"] == 0


def test_latest_per_label_supersedes_old_vote_and_can_resolve_conflict():
    rows = votes()
    rows[0]["corrected_class"] = "dos"
    conflict = build_cohort(rows)
    rows.extend(votes(reviewers=("ALICE",), first_id=3))
    resolved = build_cohort(rows)
    assert resolved.examples[0].review_ids == (2, 3)
    assert conflict.snapshot_sha256 != resolved.snapshot_sha256


@pytest.mark.parametrize("disposition", ["confirmed_attack", "needs_investigation"])
def test_unanimous_non_corrections_are_excluded(disposition):
    rows = votes()
    for row in rows:
        row.update(disposition=disposition, corrected_class=None)
    assert build_cohort(rows).summary["excluded"] == 1


@pytest.mark.parametrize(
    "reviewers,target", [(("", "bob"), "normal"), (("alice", "bob"), "unknown")]
)
def test_invalid_attribution_or_class_is_not_training_eligible(reviewers, target):
    assert build_cohort(votes(reviewers=reviewers, target=target)).summary["excluded"] == 1


def test_changed_record_or_new_same_vote_changes_snapshot():
    rows = votes()
    original = build_cohort(rows).snapshot_sha256
    rows[0]["raw_record"]["duration"] = 99
    assert build_cohort(rows).snapshot_sha256 != original
    changed = build_cohort(rows).snapshot_sha256
    rows.extend(votes(reviewers=("alice",), first_id=3))
    assert build_cohort(rows).snapshot_sha256 != changed


@pytest.mark.parametrize("balanced_rows", [40, 60, 1000])
def test_total_and_each_reviewer_budget_are_enforced(balanced_rows):
    rows = [row for i in range(10) for row in votes(i, first_id=2 * i + 1)]
    cohort = build_cohort(rows)
    plan = weight_plan(cohort, base_rows=balanced_rows, requested_weight=25)
    assert sum(plan["sample_weights"]) <= plan["total_cap"] + 1e-12
    assert sum(plan["reviewer_weights"].values()) == pytest.approx(plan["total_weight"])
    assert all(value <= plan["reviewer_cap"] + 1e-12 for value in plan["reviewer_weights"].values())
    assert plan["effective_weight"] <= 25


def test_reviewer_cap_can_bind_before_total_cap():
    cohort = build_cohort(votes())
    policy = FeedbackPolicy(reviewer_weight_fraction=0.01)
    plan = weight_plan(cohort, base_rows=100, requested_weight=25, policy=policy)
    assert plan["total_weight"] == 2 and plan["total_cap"] == 10
    assert plan["reviewer_weights"] == dict(alice=1, bob=1)


def test_large_training_set_retains_requested_weight():
    plan = weight_plan(build_cohort(votes()), base_rows=10000, requested_weight=25)
    assert plan["scale"] == 1 and plan["sample_weights"] == [25]


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum_reviewers", 1),
        ("minimum_reviewers", True),
        ("minimum_reviewers", 2.5),
        ("total_weight_fraction", 0),
        ("total_weight_fraction", float("nan")),
        ("total_weight_fraction", 1.1),
        ("total_weight_fraction", True),
        ("reviewer_weight_fraction", float("inf")),
        ("reviewer_weight_fraction", 0.2),
    ],
)
def test_invalid_policy_is_rejected(field, value):
    with pytest.raises(ValueError):
        FeedbackPolicy(**{field: value})


@pytest.mark.parametrize(
    "base_rows,weight",
    [(0, 25), (True, 25), (1.5, 25), (100, 0), (100, 61), (100, True), (100, float("nan"))],
)
def test_invalid_budget_inputs_are_rejected(base_rows, weight):
    with pytest.raises(ValueError):
        weight_plan(build_cohort(votes()), base_rows=base_rows, requested_weight=weight)


def test_empty_cohort_cannot_allocate_weights():
    with pytest.raises(ValueError, match="No consensus"):
        weight_plan(build_cohort([]), base_rows=100, requested_weight=25)


def test_missing_promotion_database_is_not_recreated(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        with locked_cohort(tmp_path / "missing.db", FeedbackPolicy()):
            pytest.fail("Missing database must not be opened")
    assert not list(tmp_path.iterdir())


def test_policy_manifest_is_plain_json_data():
    assert asdict(FeedbackPolicy()) == dict(
        minimum_reviewers=2, total_weight_fraction=0.1, reviewer_weight_fraction=0.05
    )
