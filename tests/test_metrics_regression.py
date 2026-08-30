"""Artifact-consistency checks for the committed evaluation reports.

Scope, stated precisely because the earlier version of this file overclaimed: these
tests read the committed JSON reports under ``docs/evaluation/``. They do **not** train
or evaluate anything, so they cannot detect a model regression on their own. What they
catch is documentation drift - a report regenerated with different numbers while
README.md and the evolution brief still quote the old ones, or a report deleted or
reshaped.

Catching an actual model regression requires running the evaluation, which is far too
slow for the unit suite. That belongs in a separate scheduled job, and is tracked as an
open item rather than pretended to here.

Two failure modes the previous version had, both fixed:

* It skipped when a file or key was missing, so deleting a report made the suite pass.
  These tests now fail closed.
* It located metrics by walking the JSON for the first matching key at any depth. The
  UNSW report has no top-level ``accuracy`` and two nested candidates
  (``nsl_holdout`` at 97.05% and ``unsw_transfer`` at 58.89%), so that search returned
  whichever the traversal reached first. Paths are now explicit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs" / "evaluation"

# protocol -> (json path to the metrics object, published accuracy, published macro F1)
# The path is the exact key sequence, so a schema change fails loudly instead of
# silently resolving to a different protocol's numbers.
PUBLISHED: dict[str, tuple[tuple[str, ...], float, float]] = {
    "holdout": ((), 0.9988, 0.9655),
    "cross_distribution": ((), 0.7440, 0.5149),
    "unsw_transfer": (("unsw_transfer",), 0.5889, 0.1602),
}

TOLERANCE = 0.02


def _metrics(protocol: str) -> dict:
    """Load the metrics object for a protocol. Fails closed on a missing file or key."""

    path = DOCS / protocol / "metrics.json"
    assert path.exists(), (
        f"{path} is missing. The committed evaluation artifacts are part of the "
        "published results; regenerate them rather than deleting them."
    )
    node = json.loads(path.read_text(encoding="utf-8"))
    for key in PUBLISHED[protocol][0]:
        assert isinstance(node, dict) and key in node, (
            f"{path} has no '{key}' object. The report schema changed; update the "
            "path in PUBLISHED so this check keeps reading the intended protocol."
        )
        node = node[key]
    return node


@pytest.mark.parametrize("protocol", sorted(PUBLISHED))
@pytest.mark.parametrize("metric_index,metric_key", [(1, "accuracy"), (2, "macro_f1")])
def test_committed_metric_matches_published_value(protocol, metric_index, metric_key):
    expected = PUBLISHED[protocol][metric_index]
    metrics = _metrics(protocol)
    assert metric_key in metrics, f"{protocol}/metrics.json has no '{metric_key}' key."
    actual = float(metrics[metric_key])
    assert actual == pytest.approx(expected, abs=TOLERANCE), (
        f"{protocol} {metric_key} is {actual:.4f}, published as {expected:.4f}. "
        "Regenerate the artifacts and update README.md, the evolution brief, and "
        "PUBLISHED here together if the change is intended."
    )


def test_unsw_report_distinguishes_transfer_from_its_own_holdout():
    """The UNSW report carries two accuracy figures; they must not be conflated.

    ``nsl_holdout`` is the common-feature NSL-KDD baseline, ``unsw_transfer`` is the
    cross-dataset result. Quoting the former as the transfer score would materially
    overstate generalisation.
    """

    payload = json.loads((DOCS / "unsw_transfer" / "metrics.json").read_text(encoding="utf-8"))
    assert {"nsl_holdout", "unsw_transfer"} <= set(payload)
    holdout = float(payload["nsl_holdout"]["accuracy"])
    transfer = float(payload["unsw_transfer"]["accuracy"])
    assert holdout > transfer, "UNSW common-feature holdout should exceed transfer accuracy."
    assert transfer == pytest.approx(0.5889, abs=TOLERANCE)


def test_generalisation_ordering_still_holds():
    """Hold-out > cross-distribution > transfer.

    An inversion indicates leakage between training and evaluation folds, regardless of
    whether the individual values are within tolerance.
    """

    scores = {name: float(_metrics(name)["accuracy"]) for name in PUBLISHED}
    assert scores["holdout"] > scores["cross_distribution"] > scores["unsw_transfer"], (
        f"Expected hold-out > cross-distribution > UNSW transfer, got {scores}. "
        "An inversion suggests evaluation leakage."
    )
