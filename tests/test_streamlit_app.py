"""Smoke tests that execute the Streamlit console.

These exist for three reasons.

Correctness: a stale call site once survived a refactor in ``streamlit_app.py`` and only
raised at runtime, on the branch that renders a scored connection. Unit tests over
``src`` cannot catch that, because they never execute the app script.

Measurement: coverage was previously invoked with ``--cov=streamlit_app`` while nothing
imported it, so coverage reported a ``src``-only figure. Running the app through
``AppTest`` puts it into the measured source set.

Portability: the first version of this file depended on the local NSL-KDD files, which
are gitignored. Every case failed in a clean checkout while passing locally - the exact
"works on my machine" failure these tests exist to prevent. The fixtures below build a
minimal dataset in a temp directory and run the app against it, so the suite behaves the
same in CI as it does locally.

``AppTest`` executes the real script in-process. It does not exercise the browser, so
these complement rather than replace the headless rendering checks.
"""

from __future__ import annotations


from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from streamlit.testing.v1 import AppTest

from src.ingest import NSL_KDD_COLUMNS
from src.feedback import FeedbackExample, FeedbackStore
from src.retrain import RetrainingReport

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP = str(PROJECT_ROOT / "streamlit_app.py")  # absolute: the fixtures chdir away
DEFAULT_MODEL = PROJECT_ROOT / "models" / "soc_model.joblib"
TIMEOUT = 120

WORKSPACES = ["Triage", "Review queue", "Model"]
INPUT_MODES = ["Dataset row", "JSON record", "Live replay"]

_CATEGORICAL = {"protocol_type": "tcp", "service": "http", "flag": "SF"}


def _row(label: str) -> str:
    """One schema-valid NSL-KDD record. Values are placeholders, not real telemetry."""

    fields = []
    for column in NSL_KDD_COLUMNS:
        if column in _CATEGORICAL:
            fields.append(_CATEGORICAL[column])
        elif column == "label":
            fields.append(label)
        elif column == "difficulty":
            fields.append("20")
        else:
            fields.append("0")
    return ",".join(fields)


@pytest.fixture
def app_workspace(tmp_path, monkeypatch):
    """Run the app from a directory containing a minimal NSL-KDD dataset.

    ``resolve_dataset_paths`` searches the project root first and the working directory
    second. Locally the real files win; in a clean checkout this fixture supplies them.
    Either way the app renders, so the tests do not depend on ignored local data.
    """

    data = tmp_path / "data"
    data.mkdir()
    rows = "\n".join(_row(label) for label in ("normal", "neptune", "satan")) + "\n"
    (data / "KDDTrain+.txt").write_text(rows, encoding="utf-8")
    (data / "KDDTest+.txt").write_text(rows, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def retrained_artifact_present():
    """Make the sidebar offer a second model option.

    The option list is built from ``DEFAULT_MODEL.exists()``, and the artifact is
    gitignored, so without this the "Baseline is selected by default" assertion would be
    vacuous in CI - it would assert against a single-item list.
    """

    created = False
    if not DEFAULT_MODEL.exists():
        DEFAULT_MODEL.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_MODEL.write_bytes(b"")  # existence is all the option list checks
        created = True
    try:
        yield
    finally:
        if created:
            try:
                DEFAULT_MODEL.unlink()
            except OSError:  # pragma: no cover - cleanup must not fail a passing test
                pass


def _run() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    app.run()
    return app


def _assert_clean(app: AppTest, context: str) -> None:
    exceptions = list(app.exception)
    assert not exceptions, f"{context} raised: {[str(e.value) for e in exceptions]}"


def test_app_starts_without_raising(app_workspace):
    app = _run()
    _assert_clean(app, "initial render")
    assert app.header[0].value == "Connection triage"


@pytest.mark.parametrize("workspace", WORKSPACES)
def test_each_workspace_renders_without_raising(app_workspace, workspace):
    app = _run()
    _assert_clean(app, "initial render")
    if workspace != "Triage":
        app.segmented_control[0].set_value(workspace).run()
        _assert_clean(app, f"{workspace} workspace")
    assert app.header, f"{workspace} rendered no header"


@pytest.mark.parametrize("input_mode", INPUT_MODES)
def test_each_triage_input_mode_renders(app_workspace, input_mode):
    """Two of the three input modes were never exercised by any automated check."""

    app = _run()
    app.segmented_control[1].set_value(input_mode).run()
    _assert_clean(app, f"{input_mode} input mode")


def test_missing_dataset_is_reported_and_halts(tmp_path, monkeypatch):
    """The only path that renders nothing else. Runs everywhere - no dataset needed."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "src.ingest.resolve_dataset_paths",
        lambda **kwargs: (_ for _ in ()).throw(FileNotFoundError("KDDTrain+.txt not found")),
    )
    app = _run()
    _assert_clean(app, "missing-dataset render")
    assert app.error, "expected an error message when the dataset cannot be resolved"
    assert not app.header, "app should stop before rendering a workspace header"


def test_model_operations_does_not_claim_candidate_validation(app_workspace):
    """Guards the copy corrected after review.

    The page previously claimed the candidate was compared against every evaluation
    protocol before adoption. No such comparison exists, so the wording must not return.
    """

    app = _run()
    app.segmented_control[0].set_value("Model").run()
    _assert_clean(app, "Model operations")
    captions = " ".join(caption.value for caption in app.caption)
    assert "against every evaluation protocol" not in captions
    assert "before adopting it" not in captions


def test_baseline_is_default_when_a_retrained_artifact_exists(
    app_workspace, retrained_artifact_present
):
    """Promotion must be an explicit action, not a side effect of retraining.

    The sidebar previously defaulted to the newest option, so a retrained artifact was
    adopted on the next rerun while the UI claimed promotion was not automatic. The
    artifact fixture guarantees both options are present, so this cannot pass vacuously.
    """

    app = _run()
    _assert_clean(app, "initial render")
    control = app.sidebar.segmented_control[0]
    assert "Retrained" in control.options, "fixture did not produce a second model option"
    assert control.value == "Baseline"


@pytest.fixture
def isolated_review_store(monkeypatch):
    """Keep Model operations interaction tests away from the user's SQLite state."""

    store = Mock(spec=FeedbackStore)
    store.summary.return_value = dict(total=0, reviewed=0, unreviewed=0, false_positives=0)
    store.feedback_examples.return_value = []
    store.list_tickets.return_value = []
    monkeypatch.setattr("src.feedback.FeedbackStore", lambda _path: store)
    return store


def test_model_controls_are_disabled_without_a_reviewed_cohort(
    app_workspace, isolated_review_store
):
    app = _run()
    app.segmented_control[0].set_value("Model").run()
    _assert_clean(app, "empty Model operations")
    assert app.slider(key="feedback_weight").value == 25.0
    assert app.slider(key="feedback_weight").disabled
    assert app.button(key="retrain_model").disabled
    html = " ".join(element.proto.body for element in app.get("html"))
    assert "No reviewed cohort yet" in html
    assert "not a re-evaluation of any retrained candidate" in html
    assert html.index("Cross-dataset transfer") < html.index("Cross-distribution")
    assert html.index("Cross-distribution") < html.index("Stratified hold-out")


def test_feedback_slider_value_is_passed_to_retraining(
    app_workspace, isolated_review_store, monkeypatch
):
    store = isolated_review_store
    store.feedback_examples.return_value = [FeedbackExample(7, "event-7", {}, "normal")]
    store.list_tickets.return_value = [
        SimpleNamespace(id=7, event_id="event-7", predicted_class="probe", corrected_class="normal")
    ]
    report = RetrainingReport(
        model_version="test-retrain",
        trained_at="2026-10-07T00:00:00Z",
        feedback_examples=1,
        feedback_weight=12.5,
        feedback_predictions_changed=1,
        feedback_corrected_before=0,
        feedback_corrected_after=1,
        mean_corrected_probability_before=0.1,
        mean_corrected_probability_after=0.9,
        evaluation_rows=3,
        baseline_accuracy=0.7,
        updated_accuracy=0.6,
        baseline_macro_f1=0.5,
        updated_macro_f1=0.4,
        output_model="test-only.joblib",
    )
    retrain = Mock(return_value=report)
    save = Mock()
    monkeypatch.setattr("src.retrain.retrain_from_feedback", retrain)
    monkeypatch.setattr("src.retrain.save_report", save)
    app = _run()
    app.segmented_control[0].set_value("Model").run()
    _assert_clean(app, "reviewed Model operations")
    assert not app.slider(key="feedback_weight").disabled
    html = " ".join(element.proto.body for element in app.get("html"))
    assert "#7" in html and "event-7" in html
    store.list_tickets.assert_called_once_with(review_state="false_positive", limit=6)
    app.slider(key="feedback_weight").set_value(12.5).run()
    assert not retrain.called, "adjusting the slider must not start a retraining run"
    app.button(key="retrain_model").click().run()
    _assert_clean(app, "mocked retraining")
    retrain.assert_called_once()
    assert retrain.call_args.kwargs["feedback_weight"] == 12.5
    save.assert_called_once()
    assert save.call_args.args[0] == report
    assert app.session_state.last_retrain_report["feedback_weight"] == 12.5


def test_retrained_model_warning_remains_visible(
    app_workspace, isolated_review_store, retrained_artifact_present
):
    app = _run()
    app.sidebar.segmented_control[0].set_value("Retrained").run()
    app.segmented_control[0].set_value("Model").run()
    _assert_clean(app, "active Retrained Model operations")
    assert any("replaces the model currently in use" in warning.value for warning in app.warning)


# Note on coverage. ``AppTest`` execs the script rather than importing it as a module, so
# ``streamlit_app`` never appears in ``sys.modules`` and cannot be asserted on from here.
#
# An earlier version of this comment claimed the ``--cov-fail-under`` floor guarded
# against the app dropping out of the measured set. That was asserted, not tested, and it
# was wrong: with ``--cov=streamlit_app`` an unimported app is omitted from the report
# entirely, so the floor was computed over a smaller denominator and the build passed at
# 72.06% while the application was completely unmeasured.
#
# The actual guard is ``[tool.coverage.run] source`` in pyproject.toml, which keeps
# never-executed files in the denominator at 0%. Verified by deleting this file from a
# run: the total drops from 72.31% to 64.86% and pytest exits 1.
