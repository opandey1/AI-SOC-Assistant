"""Protocol §8.1 fixtures for the rule v2 checker, on synthetic predictions only.

No model is fitted or loaded and no real evaluation rows are read. Every case builds
E, the five paired fits and the live model's predictions in memory.
"""

import json
import math
from pathlib import Path
import sys

import numpy as np
import pytest

from src import acceptance_v2 as v2
from src.acceptance_v2 import (
    CALIBRATION_SEEDS,
    CAPS,
    DECISION_SEEDS,
    LIVE,
    METRICS,
    PredictionSet,
    calibration_from_metrics,
    calibration_is_valid,
    calibration_record,
    canonical_json,
    dataset_identity,
    decide,
    digest,
    evaluation_metrics,
    input_hash,
    row_hash,
)

COUNTS = {"normal": 500, "dos": 300, "probe": 120, "r2l": 70, "u2r": 40}
TRAIN_ID = digest("synthetic training rows")
RECIPE_ID = digest("synthetic recipe")
CREATED = "2026-11-27T00:00:00Z"
MODERATE_SD = {
    "accuracy": 0.002,
    "macro_f1": 0.004,
    "recall_normal": 0.003,
    "recall_dos": 0.003,
    "recall_probe": 0.006,
    "recall_r2l": 0.01,
}
WIDE_SD = {metric: 1.0 for metric in METRICS}  # every allowance becomes its cap
EPSILON = 1e-9
# Which family to mislabel (as normal, or normal as DoS) to lower each metric.
TARGET_FAMILY = {
    "accuracy": "normal",
    "macro_f1": "normal",
    "recall_normal": "normal",
    "recall_dos": "dos",
    "recall_probe": "probe",
    "recall_r2l": "r2l",
}


def nsl_inputs(position, width=v2.INPUT_WIDTH):
    """NSL-KDD-shaped inputs: duration, three categorical fields, then counts and rates."""
    if width != v2.INPUT_WIDTH:
        return [position, position % 7][:width]
    service = ("http", "smtp", "ftp")[position % 3]
    rate = round((position % 100) / 100, 2)
    return [position, "tcp", service, "SF"] + [position % 5] * 18 + [rate] * 19


class Case:
    """E with COUNTS rows; every fit predicts the truth except U2R, caught 16 of 40."""

    def __init__(self, counts=COUNTS, *, live=True, sd=MODERATE_SD, width=v2.INPUT_WIDTH):
        self.rows = []
        self.index = {}
        for family, count in counts.items():
            self.index[family] = list(range(len(self.rows), len(self.rows) + count))
            self.rows.extend(
                (nsl_inputs(position, width), family) for position in self.index[family]
            )
        self.eval_id = dataset_identity(self.rows)
        baseline = [label for _, label in self.rows]
        for position in self.index.get("u2r", [])[16:]:
            baseline[position] = "r2l"
        self.labels = {
            (role, seed): list(baseline)
            for role in ("candidate", "baseline")
            for seed in DECISION_SEEDS
        }
        self.live = live
        if live:
            self.labels[LIVE] = list(baseline)
        self.sd = dict(sd)

    def mislabel(self, key, family, count=1, offset=0):
        to = "dos" if family == "normal" else "normal"
        for position in self.index[family][offset : offset + count]:
            self.labels[key][position] = to

    def set_u2r(self, key, caught):
        for number, position in enumerate(self.index["u2r"]):
            self.labels[key][position] = "u2r" if number in caught else "r2l"

    def predictions(self):
        return {
            key: PredictionSet.bind(labels, self.eval_id) for key, labels in self.labels.items()
        }

    def calibration(self, eval_id=None, train_id=TRAIN_ID, recipe_id=RECIPE_ID):
        return calibration_record(
            train_id=train_id,
            eval_id=eval_id or self.eval_id,
            recipe_id=recipe_id,
            sd=self.sd,
            created_at=CREATED,
        )

    def decide(self, *, rows=None, calibration=None, predictions=None, **overrides):
        arguments = dict(train_id=TRAIN_ID, recipe_id=RECIPE_ID, live_exists=self.live)
        arguments.update(overrides)
        return decide(
            self.rows if rows is None else rows,
            self.calibration() if calibration is None else calibration,
            self.predictions() if predictions is None else predictions,
            **arguments,
        )

    def loss(self, key, reference, metric):
        truth = [label for _, label in self.rows]
        before = evaluation_metrics(truth, self.labels[reference])[metric]
        after = evaluation_metrics(truth, self.labels[key])[metric]
        return before - after


def reissue(record, **changes):
    """Change a calibration record and recompute its ID, so only its content is wrong."""
    changed = json.loads(json.dumps(record))
    changed.update(changes)
    changed.pop("calibration_id")
    changed["calibration_id"] = digest(changed)
    return changed


def test_identities_follow_the_canonical_json_contract():
    assert canonical_json({"b": 1, "a": [0.1, "é"]}) == '{"a":[0.1,"é"],"b":1}'.encode("utf-8")
    with pytest.raises(ValueError):
        canonical_json([math.nan])
    assert canonical_json([np.int64(3), np.float64(0.5), np.str_("dos")]) == b'[3,0.5,"dos"]'
    assert row_hash([1, "tcp"], "dos") == digest([1, "tcp", "dos"])
    assert input_hash([1, "tcp"]) == digest([1, "tcp"])
    rows = [([1], "normal"), ([2], "dos")]
    assert dataset_identity(rows) != dataset_identity(rows[::-1])
    assert v2.checker_id() == v2.sha256(Path(v2.__file__).read_bytes()).hexdigest()


def test_calibration_record_round_trips_and_uses_seed_order_spread():
    values = {seed: {m: 0.9 + 0.001 * (seed - 3000) for m in METRICS} for seed in CALIBRATION_SEEDS}
    values[3005]["recall_r2l"] = 0.95
    record = calibration_from_metrics(
        values, train_id=TRAIN_ID, eval_id="e", recipe_id=RECIPE_ID, created_at=CREATED
    )
    expected = float(np.std([values[s]["recall_r2l"] for s in CALIBRATION_SEEDS], ddof=1))
    assert record["sd"]["recall_r2l"] == expected
    assert calibration_is_valid(record)
    assert calibration_is_valid(json.loads(json.dumps(record)))
    with pytest.raises(ValueError):
        calibration_from_metrics(
            {3001: values[3001]}, train_id="t", eval_id="e", recipe_id="r", created_at=CREATED
        )


def test_zero_spread_is_listed_and_gives_no_allowance():
    record = calibration_record(
        train_id="t",
        eval_id="e",
        recipe_id="r",
        sd=dict(MODERATE_SD, accuracy=0.0),
        created_at=CREATED,
    )
    assert record["zero_spread"] == ["accuracy"]
    assert record["allowance_average"]["accuracy"] == 0.0
    assert record["allowance_deployed"]["accuracy"] == 0.0


def test_checker_metrics_match_classification_metrics():
    from src.ingest import LABEL_MAP
    from src.retrain import classification_metrics

    rng = np.random.default_rng(7)
    families = np.array(v2.FAMILIES)
    truth = families[rng.integers(0, 5, 3000)]
    predicted = np.where(rng.random(3000) < 0.2, families[rng.integers(0, 5, 3000)], truth)
    expected = classification_metrics(
        [LABEL_MAP[x] for x in truth], [LABEL_MAP[x] for x in predicted]
    )
    actual = evaluation_metrics(truth, predicted)
    assert actual["accuracy"] == expected["accuracy"]
    assert actual["macro_f1"] == expected["macro_f1"]
    for family in v2.RECALL_FAMILIES:
        assert actual[f"recall_{family}"] == expected["recall"][family]


def test_unchanged_candidates_pass():
    decision = Case().decide()
    assert (decision.outcome, decision.reasons) == ("pass", ())
    assert decision.details["support"] == COUNTS
    assert decision.details["u2r_lost_rows"] == {}


def test_no_live_model_is_recorded_but_not_inconclusive():
    decision = Case(live=False).decide()
    assert (decision.outcome, decision.reasons) == ("pass", ("no_live_reference",))
    assert decision.details["live_change"] is None


def _boundary_case(metric, check, inside):
    """One metric sits exactly EPSILON inside or outside one allowance; others are capped."""
    case = Case(live=check != "deployed", sd=WIDE_SD)
    family = TARGET_FAMILY[metric]
    deployed = ("candidate", 42)
    if check == "deployed":
        case.mislabel(deployed, family)
        loss = case.loss(deployed, ("baseline", 42), metric)
    elif check == "live":
        case.mislabel(deployed, family)
        case.mislabel(("baseline", 42), family)
        loss = case.loss(deployed, LIVE, metric)
    else:
        for seed in DECISION_SEEDS:
            case.mislabel(("candidate", seed), family)
        loss = case.loss(deployed, ("baseline", 42), metric)
    assert 0 < loss < CAPS[metric]
    allowance = loss + EPSILON if inside else loss - EPSILON
    case.sd[metric] = allowance / 2 if check != "average" else allowance * math.sqrt(5) / 2
    return case


@pytest.mark.parametrize("inside", [True, False], ids=["inside", "outside"])
@pytest.mark.parametrize("check", ["average", "deployed", "live"])
@pytest.mark.parametrize("metric", METRICS)
def test_each_allowance_boundary(metric, check, inside):
    case = _boundary_case(metric, check, inside)
    decision = case.decide()
    expected = () if inside else (f"{check}_regressed_{metric}",)
    if check == "deployed":  # run without a live model, so check 5 records its absence
        expected += ("no_live_reference",)
    assert decision.reasons == expected
    assert decision.outcome == ("pass" if inside else "reject")


def test_allowance_tolerance_is_one_e_minus_twelve():
    case = Case(live=False, sd=WIDE_SD)
    case.mislabel(("candidate", 42), "normal")
    loss = case.loss(("candidate", 42), ("baseline", 42), "accuracy")
    case.sd["accuracy"] = (loss - 1e-13) / 2
    assert case.decide().outcome == "pass"
    case.sd["accuracy"] = (loss - 1e-11) / 2
    assert case.decide().reasons == ("deployed_regressed_accuracy", "no_live_reference")


def test_zero_spread_rejects_any_loss():
    case = Case(sd=dict(WIDE_SD, accuracy=0.0))
    assert case.decide().outcome == "pass"
    case.mislabel(("candidate", 42), "normal")
    assert case.decide().reasons == (
        "average_regressed_accuracy",
        "deployed_regressed_accuracy",
        "live_regressed_accuracy",
    )


def test_u2r_swap_with_equal_recall_is_caught_against_the_baseline():
    case = Case(sd=WIDE_SD)
    case.set_u2r(("candidate", 42), set(range(1, 17)))
    case.set_u2r(LIVE, set(range(1, 16)))
    decision = case.decide()
    assert case.loss(("candidate", 42), ("baseline", 42), "accuracy") == 0
    assert decision.reasons == ("u2r_detection_lost_baseline",)
    assert decision.details["u2r_lost_rows"] == {"baseline": [case.index["u2r"][0]]}
    assert decision.outcome == "reject"


def test_u2r_swap_is_caught_against_the_live_model():
    case = Case(sd=WIDE_SD)
    case.set_u2r(LIVE, set(range(0, 17)))
    case.set_u2r(("baseline", 42), set(range(1, 17)))
    case.set_u2r(("candidate", 42), set(range(1, 17)))
    decision = case.decide()
    assert decision.reasons == ("u2r_detection_lost_live",)
    assert decision.details["u2r_lost_rows"] == {"live": [case.index["u2r"][0]]}


def test_u2r_rows_caught_only_by_the_live_model_are_enforced():
    case = Case(sd=WIDE_SD)
    for key in [("candidate", s) for s in DECISION_SEEDS] + [
        ("baseline", s) for s in DECISION_SEEDS
    ]:
        case.set_u2r(key, set())
    case.set_u2r(LIVE, {0})
    assert case.decide().reasons == ("u2r_detection_lost_live",)
    case.set_u2r(("candidate", 42), {0})
    assert (case.decide().outcome, case.decide().reasons) == ("pass", ())


@pytest.mark.parametrize("live", [True, False])
def test_no_reference_u2r_detections_is_inconclusive(live):
    case = Case(live=live, sd=WIDE_SD)
    for key in case.labels:
        case.set_u2r(key, set())
    decision = case.decide()
    assert decision.outcome == "inconclusive"
    assert decision.reasons[-1] == "u2r_no_reference_detections"


@pytest.mark.parametrize(
    "missing, code",
    [
        ("rows", "missing_evaluation_rows"),
        ("calibration", "calibration_missing"),
        ("candidate", "missing_predictions"),
        ("live", "missing_predictions"),
    ],
)
def test_each_missing_input_alone(missing, code):
    case = Case()
    predictions = case.predictions()
    rows, calibration = case.rows, case.calibration()
    if missing == "rows":
        rows = None
    elif missing == "calibration":
        calibration = None
    elif missing == "candidate":
        del predictions[("candidate", 2003)]
    else:
        predictions[LIVE] = None
    decision = decide(
        rows, calibration, predictions, train_id=TRAIN_ID, recipe_id=RECIPE_ID, live_exists=True
    )
    assert (decision.outcome, decision.reasons) == ("error", (code,))
    assert decision.details["step"] == "presence"


def test_missing_input_stops_before_dependent_steps():
    case = Case()
    predictions = case.predictions()
    predictions[("baseline", 42)] = PredictionSet.bind(
        case.labels[("baseline", 42)][:-1], case.eval_id
    )
    decision = decide(
        case.rows, None, predictions, train_id=TRAIN_ID, recipe_id=RECIPE_ID, live_exists=True
    )
    assert decision.reasons == ("calibration_missing",)


def test_a_missing_live_prediction_is_ignored_when_there_is_no_live_model():
    case = Case(live=False)
    assert case.decide(predictions=case.predictions()).outcome == "pass"


@pytest.mark.parametrize("fault", ["length", "eval_id", "tampered", "not_a_set"])
def test_mismatched_evidence(fault):
    case = Case()
    predictions = case.predictions()
    labels = case.labels[("candidate", 2001)]
    if fault == "length":
        predictions[("candidate", 2001)] = PredictionSet.bind(labels[:-1], case.eval_id)
    elif fault == "eval_id":
        predictions[("candidate", 2001)] = PredictionSet.bind(labels, digest("another E"))
    elif fault == "tampered":
        bound = predictions[("candidate", 2001)]
        changed = list(bound.labels)
        changed[0] = "dos"
        predictions[("candidate", 2001)] = PredictionSet(
            tuple(changed), bound.eval_id, bound.sha256
        )
    else:
        predictions[("candidate", 2001)] = {"labels": labels}
    decision = case.decide(predictions=predictions)
    assert decision.reasons == ("evidence_mismatch",)
    assert decision.details["predictions"] == ["candidate:2001"]


@pytest.mark.parametrize("field_name", ["train_id", "recipe_id", "eval_id"])
def test_stale_calibration(field_name):
    case = Case()
    calibration = case.calibration(**{field_name: digest("old")})
    assert case.decide(calibration=calibration).reasons == ("calibration_stale",)


@pytest.mark.parametrize(
    "change",
    [
        {"seeds": list(range(3002, 3012))},
        {"sd": {m: 0.001 for m in METRICS if m != "recall_r2l"}},
        {"caps": dict(CAPS, recall_r2l=0.05)},
        {"allowance_deployed": dict(Case().calibration()["allowance_deployed"], accuracy=0.009)},
        {"allowance_average": dict(Case().calibration()["allowance_average"], macro_f1=0.0)},
        {"zero_spread": ["accuracy"]},
        {"sd": dict(MODERATE_SD, recall_dos=-0.001)},
        {"schema": "e3-calibration/2"},
    ],
    ids=[
        "seeds",
        "metric_keys",
        "caps",
        "allowance_deployed",
        "allowance_average",
        "zero_spread",
        "negative_sd",
        "schema",
    ],
)
def test_calibration_with_a_recomputed_id_but_wrong_content_is_invalid(change):
    case = Case()
    calibration = reissue(case.calibration(), **change)
    assert calibration["calibration_id"] == digest(
        {k: v for k, v in calibration.items() if k != "calibration_id"}
    )
    assert case.decide(calibration=calibration).reasons == ("calibration_invalid",)


def test_calibration_with_an_edited_id_or_extra_field_is_invalid():
    case = Case()
    edited = dict(case.calibration(), calibration_id=digest("other"))
    assert case.decide(calibration=edited).reasons == ("calibration_invalid",)
    extra = reissue(case.calibration(), note="extra")
    assert case.decide(calibration=extra).reasons == ("calibration_invalid",)


def test_calibration_with_non_text_ids_or_unencodable_values_is_invalid():
    case = Case()
    assert not calibration_is_valid(reissue(case.calibration(), train_id=7))
    assert not calibration_is_valid("not a record")
    unencodable = case.calibration()
    unencodable["sd"] = dict(unencodable["sd"], accuracy=math.nan)
    assert case.decide(calibration=unencodable).reasons == ("calibration_invalid",)


def test_invalid_prediction_label():
    case = Case()
    case.labels[("candidate", 2004)][0] = "unknown"
    decision = case.decide()
    assert decision.reasons == ("invalid_prediction",)
    assert decision.details["predictions"] == ["candidate:2004"]


def test_invalid_metric(monkeypatch):
    case = Case()
    original = v2.evaluation_metrics

    def broken(truth, predicted):
        return dict(original(truth, predicted), macro_f1=math.nan)

    monkeypatch.setattr(v2, "evaluation_metrics", broken)
    assert case.decide().reasons == ("invalid_metric",)


@pytest.mark.parametrize(
    "label",
    ["unknown", "worm", None, np.array(["normal", "dos"]), b"normal", "\ud800"],
    ids=["unknown", "worm", "none", "array", "bytes", "lone_surrogate"],
)
def test_valid_predictions_with_an_invalid_truth_label(label):
    case = Case()
    rows = list(case.rows)
    rows[3] = (rows[3][0], label)
    decision = case.decide(rows=rows)
    assert (decision.outcome, decision.reasons) == ("error", ("invalid_evaluation_label",))


@pytest.mark.parametrize("rows", [[], "unlabelled"], ids=["empty", "unlabelled_row"])
def test_valid_predictions_with_an_invalid_evaluation_population(rows):
    case = Case()
    if rows == "unlabelled":
        rows = list(case.rows)
        rows[5] = "normal"
    decision = case.decide(rows=rows)
    assert decision.reasons == ("invalid_evaluation_population",)


def test_changed_evaluation_rows_make_the_calibration_stale():
    case = Case()
    rows = list(case.rows)
    inputs = list(rows[0][0])
    inputs[0] = 999
    rows[0] = (inputs, rows[0][1])
    assert case.decide(rows=rows).reasons == ("calibration_stale",)


@pytest.mark.parametrize(
    "fault",
    [
        "short",
        "long",
        "nan",
        "infinity",
        "nested",
        "missing",
        "boolean",
        "mapping_inputs",
        "text_inputs",
        "lone_surrogate",
        "zero_dimensional_array",
        "two_dimensional_array",
        "bytes_input",
        "array_value",
    ],
)
def test_malformed_evaluation_rows_are_a_named_population_error(fault):
    case = Case()
    rows = list(case.rows)
    inputs = list(rows[7][0])
    changes = {
        "short": lambda: inputs[:-1],
        "long": lambda: inputs + [0],
        "nan": lambda: inputs[:5] + [math.nan] + inputs[6:],
        "infinity": lambda: inputs[:5] + [math.inf] + inputs[6:],
        "nested": lambda: inputs[:5] + [[1, 2]] + inputs[6:],
        "missing": lambda: inputs[:5] + [None] + inputs[6:],
        "boolean": lambda: inputs[:5] + [True] + inputs[6:],
        "mapping_inputs": lambda: dict(enumerate(inputs)),
        "text_inputs": lambda: "tcp",
        "lone_surrogate": lambda: inputs[:5] + ["\ud800"] + inputs[6:],
        "zero_dimensional_array": lambda: np.array(5),
        "two_dimensional_array": lambda: np.array([inputs, inputs], dtype=object).T,
        "bytes_input": lambda: inputs[:5] + [b"tcp"] + inputs[6:],
        "array_value": lambda: inputs[:5] + [np.array(1)] + inputs[6:],
    }
    rows[7] = (changes[fault](), rows[7][1])
    decision = case.decide(rows=rows)
    assert (decision.outcome, decision.reasons) == ("error", ("invalid_evaluation_population",))
    assert decision.details["malformed_rows"] == [7]


def test_a_consistently_bound_narrow_population_is_refused():
    """Two-feature rows hashed into the calibration and predictions are still refused."""
    case = Case(width=2)
    decision = case.decide()
    assert (decision.outcome, decision.reasons) == ("error", ("invalid_evaluation_population",))


def test_randomly_malformed_rows_always_get_a_named_outcome():
    """Seeded fuzz: no malformed row or label may raise; each gets a named result."""
    case = Case()
    oddities = [
        None, True, math.nan, -math.inf, "\ud800", b"x", [1], {"a": 1}, set(), object(),
        np.array(1), np.array([1, 2]), np.array(np.nan), np.bool_(True), complex(1, 2),
    ]  # fmt: skip
    odd_rows = [None, 5, "x", (1,), (1, 2, 3), np.array(1), ([1] * 41,), (np.zeros((41, 2)), "dos")]
    rng = np.random.default_rng(11)
    for _ in range(300):
        rows = list(case.rows)
        position = int(rng.integers(len(rows)))
        inputs, label = rows[position]
        choice = int(rng.integers(3))
        if choice == 0:
            inputs = list(inputs)
            inputs[int(rng.integers(41))] = oddities[int(rng.integers(len(oddities)))]
            rows[position] = (inputs, label)
        elif choice == 1:
            rows[position] = (inputs, oddities[int(rng.integers(len(oddities)))])
        else:
            rows[position] = odd_rows[int(rng.integers(len(odd_rows)))]
        decision = case.decide(rows=rows)
        assert decision.outcome == "error"
        assert decision.reasons in (
            ("invalid_evaluation_population",),
            ("invalid_evaluation_label",),
            ("invalid_evaluation_label", "invalid_evaluation_population"),
        )


@pytest.mark.skipif(not hasattr(sys, "set_int_max_str_digits"), reason="no integer-to-text limit")
def test_an_integer_beyond_the_decimal_conversion_limit_is_refused():
    """Python caps integer-to-decimal conversion, so such an input cannot be hashed."""
    original = sys.get_int_max_str_digits()
    limit = original or 4300
    sys.set_int_max_str_digits(limit)  # make sure a limit applies, then restore it
    try:
        case = Case()
        rows = list(case.rows)
        inputs = list(rows[3][0])
        inputs[4] = 10**limit  # one digit more than the runtime will write
        rows[3] = (inputs, rows[3][1])
        decision = case.decide(rows=rows)
    finally:
        sys.set_int_max_str_digits(original)
    assert (decision.outcome, decision.reasons) == ("error", ("invalid_evaluation_population",))
    assert decision.details["malformed_rows"] == [3]


def test_hashing_failure_after_validation_is_still_a_named_error(monkeypatch):
    def unencodable(rows):
        raise ValueError("cannot encode")

    monkeypatch.setattr(v2, "dataset_identity", unencodable)
    decision = Case().decide()
    assert (decision.outcome, decision.reasons) == ("error", ("invalid_evaluation_population",))


def test_rows_that_are_not_a_sequence_are_a_named_population_error():
    decision = Case().decide(rows=5)
    assert (decision.outcome, decision.reasons) == ("error", ("invalid_evaluation_population",))


def test_predictions_that_are_not_a_mapping_count_as_missing():
    case = Case()
    decision = case.decide(predictions=list(case.predictions().values()))
    assert (decision.outcome, decision.reasons) == ("error", ("missing_predictions",))


def test_a_non_text_prediction_label_with_a_valid_binding_is_an_invalid_prediction():
    case = Case()
    labels = [np.array(["normal"])] + case.labels[("candidate", 42)][1:]
    bound = PredictionSet(tuple(labels), case.eval_id, v2.prediction_digest(labels))
    decision = case.decide(predictions={**case.predictions(), ("candidate", 42): bound})
    assert (decision.outcome, decision.reasons) == ("error", ("invalid_prediction",))
    assert decision.details["predictions"] == ["candidate:42"]


def test_an_unencodable_prediction_label_is_mismatched_evidence():
    case = Case()
    labels = ["\ud800"] + case.labels[("candidate", 42)][1:]
    unbound = PredictionSet(tuple(labels), case.eval_id, "not-computable")
    decision = case.decide(predictions={**case.predictions(), ("candidate", 42): unbound})
    assert (decision.outcome, decision.reasons) == ("error", ("evidence_mismatch",))


def test_numpy_rows_are_accepted_and_hash_like_python_rows():
    case = Case()
    rows = [(np.array(inputs, dtype=object), np.str_(label)) for inputs, label in case.rows]
    rows[0] = (
        np.array([np.int64(v) if isinstance(v, int) else v for v in rows[0][0]], dtype=object),
        rows[0][1],
    )
    decision = case.decide(rows=rows)
    assert decision.outcome == "pass"
    assert decision.details["eval_id"] == case.eval_id


def test_boolean_allowances_oversized_spreads_and_float_seeds_are_invalid():
    case = Case(sd=dict(MODERATE_SD, accuracy=0.0))
    record = case.calibration()
    assert record["allowance_average"]["accuracy"] == 0.0
    boolean = reissue(
        record,
        allowance_average=dict(record["allowance_average"], accuracy=False),
        allowance_deployed=dict(record["allowance_deployed"], accuracy=False),
    )
    oversized = reissue(record, sd=dict(record["sd"], recall_r2l=10**400))
    float_seeds = reissue(record, seeds=[float(seed) for seed in CALIBRATION_SEEDS])
    for invalid in (boolean, oversized, float_seeds):
        assert not calibration_is_valid(invalid)
        assert case.decide(calibration=invalid).reasons == ("calibration_invalid",)


def test_support_below_the_minimum_skips_that_recall_and_is_inconclusive():
    counts = dict(COUNTS, r2l=20)
    case = Case(counts, sd=dict(WIDE_SD, recall_r2l=0.0))
    case.mislabel(("candidate", 42), "r2l")
    decision = case.decide()
    assert decision.details["skipped"] == ["r2l"]
    assert (decision.outcome, decision.reasons) == ("inconclusive", ("insufficient_support_r2l",))


def test_outcome_order():
    # error beats reject
    case = Case(sd=dict(WIDE_SD, accuracy=0.0))
    case.mislabel(("candidate", 42), "normal")
    case.labels[("baseline", 2001)][0] = "unknown"
    assert case.decide().outcome == "error"
    # reject beats inconclusive
    case = Case(dict(COUNTS, r2l=20), sd=dict(WIDE_SD, accuracy=0.0))
    case.mislabel(("candidate", 42), "normal")
    decision = case.decide()
    assert decision.outcome == "reject"
    assert decision.reasons[0] == "insufficient_support_r2l"
    assert "deployed_regressed_accuracy" in decision.reasons
    # inconclusive beats pass
    case = Case(dict(COUNTS, r2l=20), sd=WIDE_SD)
    assert case.decide().outcome == "inconclusive"
    assert Case(sd=WIDE_SD).decide().outcome == "pass"


def test_decisions_are_deterministic_and_report_their_inputs():
    case = Case()
    case.mislabel(("candidate", 42), "dos", count=2)
    first, second = case.decide(), case.decide()
    assert first == second
    assert first.details["eval_id"] == case.eval_id
    assert first.details["calibration_id"] == case.calibration()["calibration_id"]
    assert set(first.details["metrics"]) == {
        f"{r}:{s}" for r in ("candidate", "baseline") for s in DECISION_SEEDS
    } | {"live"}
