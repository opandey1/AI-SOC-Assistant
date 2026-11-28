"""Local consensus, bias diagnostics and actual influence budgets, not identity assurance."""

from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from src.feedback_policy import (
    FeedbackPolicy,
    build_cohort,
    check_budget_inputs,
    is_finite_number,
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


@pytest.mark.parametrize("disposition", ["confirmed_attack", "needs_investigation"])
def test_non_correction_votes_ignore_class_for_agreement_but_keep_snapshot(disposition):
    rows = votes()
    for row in rows:
        row.update(disposition=disposition, corrected_class=None)
    clean = build_cohort(rows)
    rows[1]["corrected_class"] = "dos"
    historical = build_cohort(rows)

    assert not historical.examples
    assert (
        historical.summary
        == clean.summary
        == dict(eligible=0, conflict=0, awaiting_consensus=0, excluded=1)
    )
    assert historical.reviewers == clean.reviewers
    assert all(item["peer_agreement_rate"] == 1 for item in historical.reviewers)
    assert historical.snapshot_sha256 != clean.snapshot_sha256
    assert rows[1]["corrected_class"] == "dos"


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


FAMILIES = ("normal", "dos", "probe", "r2l", "u2r")


def family_rows(count=100, **overrides):
    return dict(dict.fromkeys(FAMILIES, count), **overrides)


def cohort_of(*targets, reviewers=("alice", "bob")):
    """One consensus correction per target, in ticket order."""
    return build_cohort(
        [
            row
            for ticket, target in enumerate(targets, start=1)
            for row in votes(ticket, reviewers, target, first_id=10 * ticket)
        ]
    )


@pytest.mark.parametrize("rows", [40, 60, 1000])
def test_family_budget_caps_the_corrections_share_of_their_family(rows):
    plan = weight_plan(
        cohort_of(*["normal"] * 10), family_rows=family_rows(rows), requested_weight=25
    )
    normal = plan["families"]["normal"]
    assert set(plan["families"]) == {"normal"}
    assert normal["budget"] == pytest.approx(0.05 / 0.95 * rows)
    assert normal["total_weight"] <= normal["budget"] * (1 + 1e-12)
    # Within a family the class weight cancels: the share of its final class-weighted mass.
    assert normal["total_weight"] / (rows + normal["total_weight"]) <= 0.05 + 1e-12
    assert sum(normal["reviewer_weights"].values()) == pytest.approx(normal["total_weight"])
    assert all(
        value <= normal["reviewer_budget"] * (1 + 1e-12)
        for value in normal["reviewer_weights"].values()
    )
    assert plan["sample_weights"] == [normal["effective_weight"]] * 10
    assert normal["effective_weight"] <= 25 and plan["total_weight"] == normal["total_weight"]


def test_each_target_family_is_scaled_on_its_own():
    cohort = cohort_of("normal", "dos", "normal", "u2r")
    plan = weight_plan(cohort, family_rows=family_rows(19, dos=19000, probe=0), requested_weight=25)
    normal, dos = plan["families"]["normal"], plan["families"]["dos"]
    assert normal["budget"] == pytest.approx(1.0) and normal["scale"] == pytest.approx(1 / 50)
    assert dos["budget"] == pytest.approx(1000.0) and dos["scale"] == 1
    assert plan["families"]["u2r"]["scale"] == pytest.approx(1 / 25)
    assert plan["sample_weights"] == pytest.approx([0.5, 25, 0.5, 1.0])
    assert [example.corrected_class for example in cohort.examples] == [
        "normal",
        "dos",
        "normal",
        "u2r",
    ]
    assert plan["total_weight"] == pytest.approx(27.0)


def test_reviewer_budget_binds_only_when_set_below_half_the_family_budget():
    # Alice endorses every correction; Bob and Carol share the other endorsements.
    rows = [
        row
        for ticket in range(1, 9)
        for row in votes(ticket, ("alice", "bob" if ticket % 2 else "carol"), first_id=10 * ticket)
    ]
    cohort = build_cohort(rows)
    family = weight_plan(cohort, family_rows=family_rows(1900), requested_weight=25)
    assert family["families"]["normal"]["scale"] == pytest.approx(100 / 200)
    assert family["families"]["normal"]["reviewer_weights"]["alice"] == pytest.approx(50.0)
    strict = weight_plan(
        cohort,
        family_rows=family_rows(1900),
        requested_weight=25,
        policy=FeedbackPolicy(reviewer_budget_fraction=0.25),
    )
    assert strict["families"]["normal"]["scale"] == pytest.approx(25 / 100)
    assert strict["families"]["normal"]["reviewer_weights"]["alice"] == pytest.approx(25.0)


def test_large_training_set_retains_requested_weight():
    plan = weight_plan(cohort_of("normal"), family_rows=family_rows(10000), requested_weight=25)
    assert plan["families"]["normal"]["scale"] == 1 and plan["sample_weights"] == [25]


def test_recorded_plan_round_trips_and_matches_recomputation():
    cohort = cohort_of("normal", "dos", "dos")
    plan = weight_plan(cohort, family_rows=family_rows(77), requested_weight=12.5)
    assert json.loads(json.dumps(plan)) == plan
    assert plan == weight_plan(cohort, family_rows=family_rows(77), requested_weight=12.5)


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum_reviewers", 1),
        ("minimum_reviewers", True),
        ("minimum_reviewers", 2.5),
        ("family_mass_fraction", 0),
        ("family_mass_fraction", 1),
        ("family_mass_fraction", float("nan")),
        ("family_mass_fraction", 1.1),
        ("family_mass_fraction", True),
        ("reviewer_budget_fraction", 0),
        ("reviewer_budget_fraction", float("inf")),
        ("reviewer_budget_fraction", 1.5),
        ("reviewer_budget_fraction", True),
        ("reviewer_budget_fraction", 10**400),
    ],
)
def test_invalid_policy_is_rejected(field, value):
    with pytest.raises(ValueError):
        FeedbackPolicy(**{field: value})


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, True),
        (25, True),
        (0.5, True),
        (10**300, True),
        (10**400, False),
        (-(10**400), False),
        (float("nan"), False),
        (float("inf"), False),
        (True, False),
        ("25", False),
        (None, False),
    ],
)
def test_finite_numbers_include_large_but_not_unconvertible_integers(value, expected):
    assert is_finite_number(value) is expected


@pytest.mark.parametrize(
    "rows,weight",
    [
        (None, 25),
        ([100] * 5, 25),
        ({family: 100 for family in FAMILIES[:4]}, 25),
        (family_rows(unknown=100), 25),
        (family_rows(dos=-1), 25),
        (family_rows(dos=True), 25),
        (family_rows(dos=1.5), 25),
        (family_rows(dos=10**400), 25),
        (family_rows(), 0),
        (family_rows(), 61),
        (family_rows(), True),
        (family_rows(), float("nan")),
        (family_rows(), 10**400),
        (family_rows(), "25"),
    ],
)
def test_invalid_budget_inputs_are_rejected(rows, weight):
    with pytest.raises(ValueError):
        check_budget_inputs(rows, weight)
    with pytest.raises(ValueError):
        weight_plan(cohort_of("normal"), family_rows=rows, requested_weight=weight)


def test_corrections_need_baseline_rows_for_their_own_family_only():
    plan = weight_plan(cohort_of("dos"), family_rows=family_rows(u2r=0), requested_weight=25)
    assert set(plan["families"]) == {"dos"}
    with pytest.raises(ValueError, match="no baseline training rows"):
        weight_plan(cohort_of("u2r"), family_rows=family_rows(u2r=0), requested_weight=25)


@pytest.mark.parametrize("change", [dict(corrected_class="unknown"), dict(reviewers=("alice",))])
def test_hand_built_cohort_with_invalid_corrections_is_rejected(change):
    cohort = cohort_of("normal")
    broken = replace(cohort, examples=(replace(cohort.examples[0], **change),))
    with pytest.raises(ValueError, match="valid family"):
        weight_plan(broken, family_rows=family_rows(), requested_weight=25)


def test_empty_cohort_cannot_allocate_weights():
    with pytest.raises(ValueError, match="No consensus"):
        weight_plan(build_cohort([]), family_rows=family_rows(), requested_weight=25)


def test_missing_promotion_database_is_not_recreated(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        with locked_cohort(tmp_path / "missing.db", FeedbackPolicy()):
            pytest.fail("Missing database must not be opened")
    assert not list(tmp_path.iterdir())


def test_policy_manifest_is_plain_json_data():
    assert asdict(FeedbackPolicy()) == dict(
        minimum_reviewers=2, family_mass_fraction=0.05, reviewer_budget_fraction=0.5
    )
