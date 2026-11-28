"""Arithmetic behind the G2 weight-cap measurements (scripts/measure_weight_caps.py).

No real data file is read and no model is fitted.
"""

import hashlib
import json

import numpy as np
import pytest
from sklearn.utils.class_weight import compute_class_weight

from scripts import measure_weight_caps as caps
from src.feedback import FeedbackExample
from src.feedback_policy import FeedbackCohort, weight_plan

# Full KDDTrain+ family counts (the documented production size).
PRODUCTION = {"normal": 67343, "dos": 45927, "probe": 11656, "r2l": 995, "u2r": 52}


def cohort(size, target="normal", reviewers=("alice", "bob")):
    examples = tuple(
        FeedbackExample(
            ticket_id=i,
            event_id=f"event-{i}",
            raw_record={},
            corrected_class=target,
            review_id=i,
            review_ids=(2 * i, 2 * i + 1),
            reviewers=reviewers,
        )
        for i in range(size)
    )
    return FeedbackCohort(examples, {}, (), (), "snapshot")


@pytest.mark.parametrize("target", caps.FAMILIES)
@pytest.mark.parametrize("size, weight", [(5, 25.0), (2000, 25.0), (600, 60.0), (1, 60.0)])
def test_family_share_scale_matches_the_implemented_weight_plan(target, size, weight):
    """G2 adopted O2 at 5%: governed retraining applies the scale calculated here."""
    rows = caps.oversampled_counts(PRODUCTION)
    plan = weight_plan(cohort(size, target), family_rows=rows, requested_weight=weight)
    assert plan["families"][target]["scale"] == pytest.approx(
        caps.family_share_scale(rows[target], size, weight, 0.05)
    )


def test_status_quo_reproduces_the_documented_production_thresholds():
    oversampled = caps.oversampled_counts(PRODUCTION)
    found = {
        (mode, weight): caps.first_scaled_size(counts, PRODUCTION, "normal", weight, "status_quo")
        for mode, counts in (("oversampled", oversampled), ("original", PRODUCTION))
        for weight in (25.0, 60.0)
    }
    assert found == {
        ("oversampled", 25.0): 1347,
        ("oversampled", 60.0): 562,
        ("original", 25.0): 504,
        ("original", 60.0): 210,
    }


def test_class_weights_match_scikit_learn():
    labels = np.repeat(np.arange(5), list(PRODUCTION.values()))
    expected = compute_class_weight("balanced", classes=np.arange(5), y=labels)
    assert list(caps.class_weights(PRODUCTION).values()) == pytest.approx(list(expected))


@pytest.mark.parametrize("target", caps.FAMILIES)
@pytest.mark.parametrize("fraction", caps.FAMILY_FRACTIONS)
def test_family_share_caps_the_corrections_share_of_their_family(target, fraction):
    scale = caps.family_share_scale(PRODUCTION[target], 2000, 60.0, fraction)
    mass = caps.cohort_mass(PRODUCTION, target, 2000, 60.0 * scale)
    assert scale < 1
    assert mass["corrections"] / mass["family"] == pytest.approx(fraction)


@pytest.mark.parametrize("target", caps.FAMILIES)
def test_class_weighted_total_caps_the_corrections_share_of_all_mass(target):
    scale = caps.class_weighted_total_scale(PRODUCTION, target, 5000, 60.0)
    mass = caps.cohort_mass(PRODUCTION, target, 5000, 60.0 * scale)
    assert scale < 1
    assert mass["corrections"] / mass["total"] == pytest.approx(caps.STATUS_QUO_TOTAL_FRACTION)


def test_class_balancing_amplifies_rare_targets_only_without_oversampling():
    oversampled = caps.oversampled_counts(PRODUCTION)
    rare = caps.cohort_mass(PRODUCTION, "u2r", 5, 25.0)["class_weight"]
    common = caps.cohort_mass(PRODUCTION, "normal", 5, 25.0)["class_weight"]
    assert rare > 100 * common
    assert caps.cohort_mass(oversampled, "u2r", 5, 25.0)["class_weight"] == pytest.approx(
        1.0, abs=0.01
    )


def test_global_total_option_is_one_shared_budget():
    oversampled = caps.oversampled_counts(PRODUCTION)
    first = {
        weight: caps.first_scaled_size(
            oversampled, PRODUCTION, "normal", weight, f"global_total_{caps.GLOBAL_FRACTION:.0%}"
        )
        for weight in (25.0, 60.0)
    }
    assert first == {25.0: 135, 60.0: 57}  # 1% of 336,715 rows: 3,367 weight units
    for target in caps.FAMILIES:  # unlike the family share, the target does not matter
        assert caps.option_scales(oversampled, PRODUCTION, target, 500, 25.0)[
            "global_total_1%"
        ] == pytest.approx(3367.15 / 12500)


def test_family_share_depends_on_the_mode_for_rare_families():
    """The class weight cancels within a family, but the reference rows do not."""
    oversampled = caps.oversampled_counts(PRODUCTION)
    option = "family_share_5%"
    with_oversampling = caps.first_scaled_size(oversampled, PRODUCTION, "u2r", 25.0, option)
    without = caps.first_scaled_size(PRODUCTION, PRODUCTION, "u2r", 25.0, option)
    assert (with_oversampling, without) == (142, 1)


def test_development_rows_skip_hold_out_lines_while_parsing(tmp_path):
    """Hold-out lines are never parsed: here they are malformed and would fail."""
    valid = ",".join(["0", "tcp", "http", "SF"] + ["0"] * 37 + ["normal", "20"])
    malformed = ",".join(["sentinel"] * 60)
    held_out = [2, 5, 9]
    lines = [malformed if i in held_out else valid for i in range(12)]
    data = tmp_path / "train.txt"
    data.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"source_sha256": hashlib.sha256(data.read_bytes()).hexdigest(), "indices": held_out}
        ),
        encoding="utf-8",
    )
    development, _ = caps.load_development(data, manifest)
    assert len(development) == 9
    assert set(development["label"]) == {"normal"}
    assert not development.isin(["sentinel"]).any().any()


def test_development_rows_refuse_a_file_that_does_not_match_the_manifest(tmp_path):
    data = tmp_path / "train.txt"
    data.write_text("0\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_sha256": "0" * 64, "indices": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        caps.load_development(data, manifest)


def test_analysis_reports_every_option_mode_family_and_weight():
    result = caps.analyse(PRODUCTION)
    assert result["base_rows"] == {"oversampled": 336715, "original": 125973}
    for option in result["options"].values():
        for mode in ("oversampled", "original"):
            assert set(option[mode]) == set(caps.FAMILIES)
            assert set(option[mode]["u2r"]) == {"25", "60"}
