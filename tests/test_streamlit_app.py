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
from src.feedback import CORRECTABLE_CLASSES, FeedbackExample, FeedbackStore, TicketRecord
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
    """Keep review and Model interaction tests away from the user's SQLite state."""

    store = Mock(spec=FeedbackStore)
    store.summary.return_value = dict(total=0, reviewed=0, unreviewed=0, false_positives=0)
    store.feedback_examples.return_value = []
    store.list_tickets.return_value = []
    monkeypatch.setattr("src.feedback.FeedbackStore", lambda _path: store)
    return store


@pytest.fixture
def review_tickets(isolated_review_store):
    store = isolated_review_store
    store.summary.return_value = dict(total=2, reviewed=0, unreviewed=2, false_positives=0)
    store.list_tickets.return_value = [
        TicketRecord(
            id=ticket_id,
            event_id=f"event-{ticket_id}",
            observed_at="2026-10-09T10:00:00Z",
            created_at="2026-10-09T10:00:00Z",
            source="test-only",
            source_ip="192.0.2.7",
            predicted_class="probe",
            rf_confidence=0.9,
            fused_confidence=0.8,
            alert_reason="both",
            raw_record={},
            evidence={"predicted_class": "probe"},
            ticket_text="Test-only incident ticket",
            model_version="test-model",
            disposition=None,
            corrected_class=None,
            analyst_notes=None,
            reviewed_by=None,
            reviewed_at=None,
        )
        for ticket_id in (7, 8)
    ]
    return store


def _review_app() -> AppTest:
    app = _run()
    app.segmented_control[0].set_value("Review queue").run()
    _assert_clean(app, "Review queue")
    return app


def _select_ticket(app: AppTest, row: int = 0) -> AppTest:
    # AppTest serialises Radio/Pills state, but its Dataframe is not a Widget.
    # Re-inject the browser's row selection for each selected-ticket interaction.
    app.session_state[app.dataframe[0].key] = {
        "selection": {"rows": [row], "columns": [], "cells": []}
    }
    app.run()
    _assert_clean(app, "selected ticket")
    return app


def test_review_empty_queue_keeps_detail_empty_state(app_workspace, isolated_review_store):
    app = _review_app()
    html = " ".join(element.proto.body for element in app.get("html"))
    assert "No tickets match this review state" in html
    assert "No ticket selected" in html
    captions = " ".join(c.value for c in app.caption)
    assert "Only generated alert tickets are stored" in captions
    assert "Every scored connection" not in captions
    assert not app.radio and not app.pills
    isolated_review_store.record_review.assert_not_called()


def test_review_no_selection_and_stale_row_do_not_render_form(app_workspace, review_tickets):
    app = _review_app()
    assert not app.radio
    _select_ticket(app, row=99)
    assert not app.radio
    assert "No ticket selected" in " ".join(e.proto.body for e in app.get("html"))
    review_tickets.record_review.assert_not_called()


def test_review_requires_explicit_disposition(app_workspace, review_tickets):
    app = _select_ticket(_review_app())
    assert app.radio[0].value is None
    assert app.pills[0].disabled
    assert app.button[-1].disabled
    assert "Correction feeds weighted retraining" in app.radio[0].proto.captions[1]
    app.radio[0].set_value("false_positive")
    _select_ticket(app)
    _assert_clean(app, "false-positive draft")
    assert not app.pills[0].disabled
    assert not app.button[-1].disabled
    review_tickets.record_review.assert_not_called()


@pytest.mark.parametrize("family", CORRECTABLE_CLASSES)
def test_review_false_positive_submits_selected_family(app_workspace, review_tickets, family):
    app = _select_ticket(_review_app())
    app.radio[0].set_value("false_positive")
    _select_ticket(app)
    app.pills[0].set_value(family)
    app.main.text_input[0].set_value("alice")
    app.text_area[0].set_value("Approved maintenance")
    app.button[-1].click()
    _select_ticket(app)
    _assert_clean(app, "saved false-positive review")
    review_tickets.record_review.assert_called_once_with(
        7,
        disposition="false_positive",
        corrected_class=family,
        analyst_notes="Approved maintenance",
        reviewed_by="alice",
    )


@pytest.mark.parametrize("disposition", ["confirmed_attack", "needs_investigation"])
def test_non_feedback_review_does_not_store_a_correction(
    app_workspace, review_tickets, disposition
):
    app = _select_ticket(_review_app())
    app.radio[0].set_value("false_positive")
    _select_ticket(app)
    app.pills[0].set_value("dos")
    _select_ticket(app)
    app.radio[0].set_value(disposition)
    _select_ticket(app)
    assert app.pills[0].disabled
    app.main.text_input[0].set_value("alice")
    app.button[-1].click()
    _select_ticket(app)
    _assert_clean(app, "saved non-feedback review")
    assert review_tickets.record_review.call_args.kwargs["corrected_class"] is None
    assert review_tickets.record_review.call_args.kwargs["disposition"] == disposition


def test_review_blank_analyst_is_rejected_without_losing_notes(app_workspace, review_tickets):
    app = _select_ticket(_review_app())
    app.radio[0].set_value("false_positive")
    _select_ticket(app)
    app.main.text_input[0].set_value("  ")
    app.text_area[0].set_value("Keep this draft")
    app.button[-1].click()
    _select_ticket(app)
    _assert_clean(app, "invalid analyst")
    assert "Enter an analyst name" in app.error[0].value
    assert app.text_area[0].value == "Keep this draft"
    review_tickets.record_review.assert_not_called()


def test_review_ticket_and_database_changes_isolate_drafts(app_workspace, review_tickets):
    app = _select_ticket(_review_app())
    first_key = app.radio[0].key
    app.radio[0].set_value("false_positive")
    _select_ticket(app)
    app.text_area[0].set_value("Ticket seven only")
    _select_ticket(app)
    _select_ticket(app, row=1)
    assert app.radio[0].key != first_key
    assert app.radio[0].value is None
    assert app.text_area[0].value == ""
    app.sidebar.text_input[0].set_value("other-review-database.db").run()
    _assert_clean(app, "database switch")
    assert not app.radio, "selection must reset when the database changes"
    _select_ticket(app, row=0)
    assert app.radio[0].key != first_key
    assert app.radio[0].value is None
    review_tickets.record_review.assert_not_called()


def test_review_filter_change_clears_selection(app_workspace, review_tickets):
    app = _select_ticket(_review_app())
    app.segmented_control(key="review_state").set_value("all").run()
    _assert_clean(app, "review filter change")
    assert not app.radio
    review_tickets.list_tickets.assert_called_with(review_state="all", limit=200)


def test_review_queue_row_changes_reset_table_identity(app_workspace, review_tickets):
    app = _select_ticket(_review_app())
    previous_table_key = app.dataframe[0].key
    review_tickets.list_tickets.return_value.reverse()
    app.run()
    _assert_clean(app, "reordered queue")
    assert app.dataframe[0].key != previous_table_key
    assert not app.radio


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
