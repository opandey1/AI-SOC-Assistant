"""Guard the committed evaluation metrics against silent regression.

CI verifies the code, not the model. A change to preprocessing, class balancing, or the
fusion rule can leave every unit test green while moving the published numbers. These
tests fail if a committed metric drifts beyond tolerance, so a regression has to be
acknowledged and the artifacts regenerated deliberately rather than noticed later.

Tolerances are deliberately loose. The goal is catching a structural break, not pinning
floating-point noise across platforms and library patch versions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs" / "evaluation"

# protocol -> (accuracy, macro F1) as published in README.md and the evolution brief.
PUBLISHED = {
    "holdout": (0.9988, 0.9655),
    "cross_distribution": (0.7440, 0.5149),
    "unsw_transfer": (0.5889, 0.1602),
}

ACCURACY_TOLERANCE = 0.02
MACRO_F1_TOLERANCE = 0.02


def _load(protocol: str) -> dict:
    path = DOCS / protocol / "metrics.json"
    if not path.exists():
        pytest.skip(f"{path} not generated in this checkout")
    return json.loads(path.read_text(encoding="utf-8"))


def _find(payload: dict, *names: str) -> float | None:
    """Locate a metric by any of its known key spellings, at any nesting depth."""

    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in names and isinstance(value, (int, float)):
                    return float(value)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(item for item in current if isinstance(item, (dict, list)))
    return None


@pytest.mark.parametrize("protocol", sorted(PUBLISHED))
def test_committed_accuracy_matches_published_value(protocol):
    expected, _ = PUBLISHED[protocol]
    actual = _find(_load(protocol), "accuracy", "overall_accuracy")
    if actual is None:
        pytest.skip(f"no accuracy key in {protocol}/metrics.json")
    assert actual == pytest.approx(expected, abs=ACCURACY_TOLERANCE), (
        f"{protocol} accuracy moved from the published {expected:.2%} to {actual:.2%}. "
        "Regenerate the artifacts and update README.md, the evolution brief, and "
        "PUBLISHED in this test if the change is intended."
    )


@pytest.mark.parametrize("protocol", sorted(PUBLISHED))
def test_committed_macro_f1_matches_published_value(protocol):
    _, expected = PUBLISHED[protocol]
    actual = _find(_load(protocol), "macro_f1", "macro_avg_f1", "macro f1")
    if actual is None:
        pytest.skip(f"no macro F1 key in {protocol}/metrics.json")
    assert actual == pytest.approx(expected, abs=MACRO_F1_TOLERANCE), (
        f"{protocol} macro F1 moved from the published {expected:.4f} to {actual:.4f}. "
        "Regenerate the artifacts and update the published figures if intended."
    )


def test_generalisation_ordering_still_holds():
    """Hold-out must stay above cross-distribution, which must stay above transfer.

    If this inverts, something is structurally wrong - most likely leakage between the
    training and evaluation folds - regardless of the individual values.
    """

    scores = {}
    for protocol in PUBLISHED:
        value = _find(_load(protocol), "accuracy", "overall_accuracy")
        if value is None:
            pytest.skip(f"no accuracy key in {protocol}/metrics.json")
        scores[protocol] = value

    assert scores["holdout"] > scores["cross_distribution"] > scores["unsw_transfer"], (
        "Expected hold-out > cross-distribution > UNSW transfer, got "
        f"{scores}. An inversion suggests evaluation leakage."
    )
