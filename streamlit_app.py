"""Interactive SOC analyst console for triage, review, and model updates."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from src import ui
from src.feedback import CORRECTABLE_CLASSES, REVIEW_DISPOSITIONS, FeedbackStore
from src.ingest import MODEL_INPUT_COLUMNS, NSL_KDD_COLUMNS, load_nsl_kdd, resolve_dataset_paths
from src.model_store import MODEL_ARTIFACT_FORMAT, load_model_artifact, runtime_from_artifact
from src.model_registry import ModelRegistry
from src.retrain import MINIMUM_FEEDBACK_EXAMPLES
from src.runtime import (
    ConnectionAnalysis,
    analyze_raw_connection,
    build_runtime,
    jsonable_record,
)
from src.streaming import ConnectionEvent, event_from_payload, replay_events

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = PROJECT_ROOT / "state" / "soc_feedback.db"
DEFAULT_MODEL = PROJECT_ROOT / "models" / "soc_model.joblib"
REGISTRY_ROOT = Path(os.environ.get("SOC_MODEL_REGISTRY", PROJECT_ROOT / "models" / "registry"))
LOGGER = logging.getLogger(__name__)

# Published evaluation results (docs/evaluation/*). Reported hardest-first.
EVALUATION_PROTOCOLS = (
    (
        "Cross-dataset transfer",
        "UNSW-NB15 · 63,461 rows",
        0.5889,
        0.1602,
        ui.TOKENS["status-alert"],
        "Zero tuning across a different capture, feature definition, and attack taxonomy.",
    ),
    (
        "Cross-distribution",
        "KDDTest+ · 22,544 rows",
        0.7440,
        0.5149,
        ui.TOKENS["status-warn"],
        "Novel attack variants absent from the training split.",
    ),
    (
        "Stratified hold-out",
        "KDDTrain+ · 25,195 rows",
        0.9988,
        0.9655,
        ui.TOKENS["status-ok"],
        "In-distribution ceiling. Listed last — it invites variant-leakage skepticism.",
    ),
)

st.set_page_config(
    page_title="SOC analyst console",
    page_icon=":material/security:",
    layout="wide",
)
st.html(ui.CSS)


@st.cache_resource(max_entries=3, show_spinner=False)
def get_runtime(train_path: str, test_path: str, model_path: str | None):
    if model_path:
        dataset = load_nsl_kdd(train_path, test_path)
        artifact = load_model_artifact(model_path)
        return runtime_from_artifact(artifact, dataset=dataset)
    return build_runtime(train_path, test_path)


@st.cache_data(max_entries=32, show_spinner=False)
def load_raw_test_row(test_path: str, row_index: int) -> dict[str, object]:
    frame = pd.read_csv(
        test_path,
        names=NSL_KDD_COLUMNS,
        skiprows=row_index,
        nrows=1,
    )
    if frame.empty:
        raise IndexError("The selected row is outside the test dataset.")
    return jsonable_record(frame.iloc[0][MODEL_INPUT_COLUMNS])


@st.cache_data(max_entries=4, show_spinner=False)
def count_rows(dataset_path: str) -> int:
    with Path(dataset_path).open(encoding="utf-8") as dataset_file:
        return sum(1 for _ in dataset_file)


def persist_alert(
    store: FeedbackStore,
    event: ConnectionEvent,
    analysis: ConnectionAnalysis,
) -> int | None:
    if not analysis.ticket:
        return None
    return store.log_analysis(
        analysis,
        event_id=event.event_id,
        source=event.source,
        observed_at=event.observed_at,
    )


def show_analysis(analysis: ConnectionAnalysis, ticket_id: int | None) -> None:
    score = analysis.score
    rf_class = str(analysis.evidence.get("rf_predicted_class", analysis.predicted_class))
    st.html(
        ui.verdict_card(
            predicted_class=analysis.predicted_class,
            is_alert=score.fused_anomaly,
            fused_confidence=score.fused_confidence,
            rf_confidence=score.rf_confidence,
            isolation_risk=score.isolation_risk,
            isolation_score=score.isolation_score,
            isolation_threshold=float(analysis.evidence.get("isolation_threshold", 0.7)),
            alert_reason=score.alert_reason,
        )
    )

    evidence_col, ticket_col = st.columns([1.05, 1], gap="medium")

    with evidence_col:
        evidence_body = ""
        if score.isolation_anomaly:
            evidence_body += ui.callout(
                "Isolation Forest signal",
                f"Risk {score.isolation_risk:.1%} against the configured threshold. "
                f"Raw decision score {score.isolation_score:.6f}, "
                "where lower is more anomalous.",
                ui.TOKENS["status-warn"],
            )
        evidence_body += ui.evidence_rows(
            analysis.evidence.get("top_shap_drivers", []), predicted_class=rf_class
        )
        st.html(
            ui.card(
                "Why this was flagged" if score.fused_anomaly else "Why this was cleared",
                f"SHAP evidence for Random Forest class: {rf_class}.",
                evidence_body,
            )
        )

        with st.expander("Scoring details", icon=":material/data_object:"):
            st.json(analysis.evidence)

    with ticket_col:
        st.html(ui.panel_header("Incident ticket"))
        if analysis.ticket:
            if ticket_id is not None:
                st.html(ui.pill(f"STORED AS TICKET #{ticket_id}", ui.TOKENS["accent"], dot=False))
            st.markdown(analysis.ticket)
            st.download_button(
                "Download ticket",
                data=analysis.ticket,
                file_name=f"incident_ticket_{ticket_id or 'draft'}.md",
                mime="text/markdown",
                icon=":material/download:",
            )
        else:
            st.html(
                ui.callout(
                    "Normal verdict",
                    "Fused scoring cleared this connection. No incident ticket was generated "
                    "and nothing was written to the review queue.",
                    ui.TOKENS["status-ok"],
                )
            )


def show_replay_feed(rows: list[dict[str, object]], requested: int) -> None:
    st.html(ui.panel_header("Replay feed", f"{len(rows)} processed / {requested} requested"))
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        height=min(360, 44 + 35 * len(rows)),
        column_config={
            "Confidence": st.column_config.ProgressColumn(
                min_value=0.0, max_value=1.0, format="percent"
            )
        },
    )


def reset_triage_result() -> None:
    for key, value in {
        "last_analysis": None,
        "last_ticket_id": None,
        "last_event": None,
        "triage_error": None,
        "replay_rows": [],
        "replay_requested": 0,
        "triage_request_mode": None,
    }.items():
        st.session_state[key] = value


for key, default_value in {
    "last_analysis": None,
    "last_ticket_id": None,
    "last_retrain_report": None,
    "last_event": None,
    "triage_error": None,
    "replay_rows": [],
    "replay_requested": 0,
    "triage_request_mode": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

try:
    dataset_paths = resolve_dataset_paths(search_roots=[PROJECT_ROOT, Path.cwd()])
except FileNotFoundError as exc:
    st.error(str(exc), icon=":material/error:")
    st.stop()

registry = ModelRegistry(REGISTRY_ROOT, legacy_model=DEFAULT_MODEL)
try:
    registry_state = registry.state()
    selected_artifact = registry.active_path()
except (ValueError, OSError, KeyError, sqlite3.Error):
    st.error(
        "The active model registry could not be verified. Inspect local model storage.",
        icon=":material/error:",
    )
    st.stop()

with st.sidebar:
    st.html(ui.section_label("SESSION"))
    model_options = ["Baseline"]
    if selected_artifact is not None:
        model_options.append("Retrained")
    model_mode = st.segmented_control(
        "Model",
        model_options,
        # Candidate training never changes this registry-selected artifact.
        default=model_options[0],
        width="stretch",
    )
    provider = st.selectbox(
        "Ticket provider",
        ["template", "ollama", "openai", "anthropic"],
        index=0,
    )
    database_value = st.text_input("Review database", value=str(DEFAULT_DATABASE))

    st.html(ui.section_label("DATASET"))
    st.html(
        ui.kv_block(
            [
                ("Training rows", f"{count_rows(str(dataset_paths.train)):,}", None),
                ("Evaluation file", dataset_paths.test.name, None),
                ("Model families", "5", None),
            ]
        )
    )

result_database = str(Path(database_value).resolve())
if st.session_state.get("triage_database") != result_database:
    reset_triage_result()
    st.session_state.triage_database = result_database
store = FeedbackStore(Path(database_value))
active_model_path = str(selected_artifact) if model_mode == "Retrained" else None
queue_summary = store.summary()

st.html(
    ui.topbar(
        model_version="retrained" if active_model_path else "baseline-nsl-kdd",
        provider=provider,
    )
)

view = st.segmented_control(
    "Workspace",
    ["Triage", "Review queue", "Model"],
    default="Triage",
    required=True,
    width="stretch",
)

if view == "Triage":
    with st.container(key="triage_workspace", gap=18):
        st.header("Connection triage")
        st.caption(
            "NSL-KDD-shaped connection data. Only generated alert tickets enter the review queue."
        )
        input_mode = st.segmented_control(
            "Input",
            ["Dataset row", "JSON record", "Live replay"],
            default="Dataset row",
            required=True,
            key="triage_input",
        )
        test_rows = count_rows(str(dataset_paths.test))
        if not test_rows:
            st.error("The test dataset contains no connection rows.", icon=":material/error:")
            st.stop()
        source_ip = "192.0.2.47"
        row_index, replay_count, replay_delay, raw_json = 0, min(5, test_rows), 0.5, ""
        if input_mode == "Live replay":
            st.caption(
                "Delayed NSL-KDD dataset replay, not live network telemetry. Source IPs are assigned per event."
            )
        with st.container(key="triage_controls"), st.form(f"triage_form_{input_mode}"):
            if input_mode != "Live replay":
                source_ip = st.text_input("Source IP", value=source_ip, key="triage_source_ip")
            if input_mode == "Dataset row":
                row_index = int(
                    st.number_input(
                        "Test row",
                        min_value=0,
                        max_value=max(0, test_rows - 1),
                        value=0,
                        step=1,
                        key="triage_row",
                    )
                )
            elif input_mode == "JSON record":
                default_record = load_raw_test_row(str(dataset_paths.test), 0)
                raw_json = st.text_area(
                    "Connection JSON",
                    value=json.dumps(default_record, indent=2),
                    height=320,
                    key="triage_json",
                    persist_state="session",
                )
            else:
                with st.container(horizontal=True):
                    row_index = int(
                        st.number_input(
                            "Start row",
                            min_value=0,
                            max_value=max(0, test_rows - 1),
                            value=0,
                            step=1,
                            key="triage_replay_start",
                        )
                    )
                    replay_count = int(
                        st.number_input(
                            "Events",
                            min_value=1,
                            max_value=20,
                            value=max(1, replay_count),
                            step=1,
                            key="triage_replay_count",
                        )
                    )
                    replay_delay = float(
                        st.number_input(
                            "Interval (seconds)",
                            min_value=0.0,
                            max_value=5.0,
                            value=0.5,
                            step=0.1,
                            key="triage_replay_delay",
                        )
                    )
            submitted = st.form_submit_button(
                "Start replay" if input_mode == "Live replay" else "Analyze connection",
                type="primary",
                icon=":material/play_arrow:",
                key="triage_submit",
            )

        feed_slot = st.empty()
        if submitted:
            # A failed new request must not leave the previous verdict looking current.
            reset_triage_result()
            st.session_state.triage_request_mode = input_mode
            st.session_state.replay_requested = replay_count if input_mode == "Live replay" else 0
            stage = "input"
            with st.status("Validating connection input", expanded=True) as status:
                try:
                    event = None
                    if input_mode == "JSON record":
                        record = json.loads(raw_json)
                        if not isinstance(record, dict):
                            raise ValueError("Connection JSON must be an object.")
                        event = event_from_payload(
                            {
                                "connection": record,
                                "source_ip": source_ip,
                                "source": "dashboard-json",
                                "event_id": f"dashboard-json-{uuid4()}",
                            }
                        )
                    elif not 0 <= row_index < test_rows:
                        raise IndexError("The selected row is outside the test dataset.")
                    elif input_mode == "Dataset row":
                        event = ConnectionEvent(
                            event_id=f"dashboard-row-{row_index}-{uuid4()}",
                            observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            source_ip=source_ip,
                            source="dashboard-row",
                            record=load_raw_test_row(str(dataset_paths.test), row_index),
                        )
                    stage = "runtime"
                    status.update(label="Loading detection runtime")
                    runtime = get_runtime(
                        str(dataset_paths.train), str(dataset_paths.test), active_model_path
                    )
                    status.write(f"Active model: {runtime.model_version}")
                    events = (
                        replay_events(
                            runtime.dataset.test,
                            start_index=row_index,
                            limit=replay_count,
                            delay=replay_delay,
                        )
                        if input_mode == "Live replay"
                        else [event]
                    )
                    progress = st.progress(0.0) if input_mode == "Live replay" else None
                    available = min(replay_count, test_rows - row_index)
                    stage = "inference"
                    for event in events:
                        stage = "inference"
                        analysis = analyze_raw_connection(
                            event.record,
                            runtime=runtime,
                            source_ip=event.source_ip,
                            provider=provider,
                        )
                        stage = "storage"
                        ticket_id = persist_alert(store, event, analysis)
                        st.session_state.last_analysis = analysis
                        st.session_state.last_ticket_id = ticket_id
                        st.session_state.last_event = {
                            "id": event.event_id,
                            "source": event.source,
                            "source_ip": analysis.source_ip,
                            "model": analysis.model_version,
                        }
                        if input_mode == "Live replay":
                            st.session_state.replay_rows.append(
                                {
                                    "Event": event.event_id,
                                    "Source IP": event.source_ip,
                                    "Class": analysis.predicted_class,
                                    "Confidence": analysis.score.fused_confidence,
                                    "Verdict": analysis.verdict,
                                }
                            )
                            processed = len(st.session_state.replay_rows)
                            progress.progress(
                                processed / available,
                                text=f"{processed}/{available} available events",
                            )
                            with feed_slot.container():
                                show_replay_feed(st.session_state.replay_rows, replay_count)
                            status.write(
                                f"{event.event_id}: {analysis.predicted_class} / {analysis.verdict}"
                            )
                    label = (
                        f"Replay complete: {len(st.session_state.replay_rows)} of {replay_count} requested"
                        if input_mode == "Live replay"
                        else "Analysis complete"
                    )
                    status.update(label=label, state="complete", expanded=False)
                except Exception as exc:
                    # Do not echo internal errors, connection payloads, or provider secrets.
                    LOGGER.warning("triage_failed stage=%s type=%s", stage, type(exc).__name__)
                    if stage == "input" and isinstance(exc, (ValueError, IndexError)):
                        message = f"Invalid connection input: {exc}"
                    elif stage == "inference" and isinstance(exc, ValueError):
                        message = "Connection could not be scored. Required feature values must be scalar and finite."
                    else:
                        message = "Analysis could not complete. Check the local runtime or storage configuration."
                    st.session_state.triage_error = message
                    status.update(
                        label="Replay failed" if input_mode == "Live replay" else "Analysis failed",
                        state="error",
                        expanded=True,
                    )

        if st.session_state.replay_rows:
            with feed_slot.container():
                show_replay_feed(st.session_state.replay_rows, st.session_state.replay_requested)
        if st.session_state.triage_error:
            st.error(
                f"{st.session_state.triage_request_mode}: {st.session_state.triage_error}",
                icon=":material/error:",
            )
            if st.session_state.replay_rows:
                st.warning(
                    f"Replay stopped after {len(st.session_state.replay_rows)} processed event(s). Already stored alert tickets are retained; remaining events were not processed."
                )
        if st.session_state.last_analysis is not None:
            context = st.session_state.last_event
            st.caption(
                f"Last scored event: {context['id']} | {context['source']} | {context['source_ip']} | model {context['model']}"
            )
            show_analysis(st.session_state.last_analysis, st.session_state.last_ticket_id)
        else:
            st.html(
                ui.empty_state(
                    ui.ICON_SCAN,
                    "No connection scored yet",
                    "No current verdict, SHAP evidence or incident ticket is available.",
                )
            )

elif view == "Review queue":
    with st.container(key="review_workspace", gap=18):
        st.header("Analyst review queue")
        st.caption(
            "Only generated alert tickets are stored in SQLite; cleared connections are not. "
            "Reviews are append-only: the latest disposition wins and the history is preserved."
        )
        st.html(
            ui.tile_row(
                [
                    ui.tile("TICKETS", f"{queue_summary['total']:,}", "all time"),
                    ui.tile(
                        "UNREVIEWED",
                        f"{queue_summary['unreviewed']:,}",
                        "awaiting analyst",
                        color=ui.TOKENS["status-warn"],
                    ),
                    ui.tile(
                        "REVIEWED",
                        f"{queue_summary['reviewed']:,}",
                        "dispositioned",
                        color=ui.TOKENS["status-ok"],
                    ),
                    ui.tile(
                        "FALSE POSITIVES",
                        f"{queue_summary['false_positives']:,}",
                        "feed retraining",
                        color=ui.TOKENS["status-info"],
                    ),
                ]
            )
        )
        review_state = st.segmented_control(
            "Review state",
            ["all", "unreviewed", *REVIEW_DISPOSITIONS],
            default="unreviewed",
            required=True,
            key="review_state",
            format_func=lambda value: value.replace("_", " ").capitalize(),
        )
        tickets = store.list_tickets(review_state=review_state, limit=200)
        # Scope drafts and row selection to the database as well as the ticket/filter.
        database_scope = sha256(str(Path(database_value).resolve()).encode()).hexdigest()[:16]
        queue_revision = sha256(
            ",".join(str(ticket.id) for ticket in tickets).encode()
        ).hexdigest()[:16]
        table_col, detail_col = st.columns([1.5, 1], gap="medium")
        selected_rows = []
        with table_col:
            st.html(ui.panel_header("Alert tickets", f"{len(tickets)} shown / latest 200"))
            if not tickets:
                st.html(
                    ui.empty_state(
                        ui.ICON_QUEUE,
                        "No tickets match this review state",
                        "No stored alert tickets are available for this filter.",
                    )
                )
            else:
                queue = pd.DataFrame(
                    [
                        {
                            "ID": ticket.id,
                            "Observed": ticket.observed_at,
                            "Source IP": ticket.source_ip,
                            "Class": ticket.predicted_class,
                            "Confidence": ticket.fused_confidence,
                            "Status": ticket.disposition or "unreviewed",
                        }
                        for ticket in tickets
                    ]
                )
                selection = st.dataframe(
                    queue,
                    hide_index=True,
                    key=f"review_queue_table_{database_scope}_{review_state}_{queue_revision}",
                    on_select="rerun",
                    selection_mode="single-row",
                    height=min(460, 44 + 35 * len(queue)),
                    column_config={
                        "ID": st.column_config.NumberColumn(format="#%d", pinned=True),
                        "Confidence": st.column_config.ProgressColumn(
                            min_value=0.0,
                            max_value=1.0,
                            format="percent",
                        ),
                    },
                )
                selected_rows = selection.selection.rows

        with detail_col, st.container(key="review_detail", gap=14):
            st.html(ui.panel_header("Analyst disposition"))
            if not selected_rows or not 0 <= selected_rows[0] < len(tickets):
                st.html(
                    ui.empty_state(
                        ui.ICON_QUEUE,
                        "No ticket selected",
                        "No analyst disposition is being drafted. "
                        "Eligible false-positive corrections join the retraining cohort.",
                    )
                )
            else:
                selected = tickets[selected_rows[0]]
                review_key = f"review_{database_scope}_{selected.id}"
                st.html(
                    ui.card(
                        f"Ticket #{selected.id}",
                        "",
                        ui.family_chip(selected.predicted_class)
                        + '<div style="height:10px"></div>'
                        + ui.kv_block(
                            [
                                ("Event", selected.event_id, None),
                                ("Source IP", selected.source_ip, None),
                                ("Model", selected.model_version, None),
                                (
                                    "Fused confidence",
                                    f"{selected.fused_confidence:.1%}",
                                    ui.TOKENS["accent"],
                                ),
                                (
                                    "Latest disposition",
                                    (selected.disposition or "unreviewed").replace("_", " "),
                                    None,
                                ),
                            ]
                        ),
                    )
                )
                with st.container(key="review_disposition"):
                    disposition = st.radio(
                        "Disposition",
                        REVIEW_DISPOSITIONS,
                        index=None,
                        key=f"{review_key}_disposition",
                        format_func=lambda value: value.replace("_", " ").capitalize(),
                        captions=[
                            "Validated malicious activity. Excluded from feedback retraining.",
                            "Incorrect alert or family label. Correction feeds weighted retraining.",
                            "Evidence is inconclusive. Excluded from feedback retraining.",
                        ],
                        width="stretch",
                    )
                # Disposition reruns immediately; draft fields are batched until Save.
                with st.form(f"{review_key}_form", border=False):
                    with st.container(key="review_corrected_class"):
                        corrected_class = st.pills(
                            "Corrected class",
                            CORRECTABLE_CLASSES,
                            default="normal",
                            required=True,
                            key=f"{review_key}_corrected_class",
                            format_func=str.upper,
                            disabled=disposition != "false_positive",
                        )
                    analyst = st.text_input("Analyst", key=f"{review_key}_analyst")
                    notes = st.text_area("Notes", key=f"{review_key}_notes", height=100)
                    review_submitted = st.form_submit_button(
                        "Record review",
                        type="primary",
                        icon=":material/save:",
                        disabled=disposition is None,
                        key=f"{review_key}_submit",
                        width="stretch",
                    )
                if review_submitted:
                    if disposition not in REVIEW_DISPOSITIONS:
                        st.error("Choose a disposition before recording a review.")
                    elif (
                        disposition == "false_positive"
                        and corrected_class not in CORRECTABLE_CLASSES
                    ):
                        st.error("Choose a corrected class for the false-positive review.")
                    elif not analyst.strip():
                        st.error("Enter an analyst name before recording a review.")
                    else:
                        store.record_review(
                            selected.id,
                            disposition=disposition,
                            corrected_class=(
                                corrected_class if disposition == "false_positive" else None
                            ),
                            analyst_notes=notes,
                            reviewed_by=analyst,
                        )
                        st.toast("Review saved", icon=":material/check_circle:")
                        st.rerun()

                with st.expander("Incident ticket", icon=":material/article:"):
                    st.markdown(selected.ticket_text)
                with st.expander("Evidence bundle", icon=":material/data_object:"):
                    st.json(selected.evidence)

else:
    with st.container(key="model_operations", gap=18):
        st.header("Model operations")
        st.caption(
            "Fold analyst-reviewed false positives into a weighted retraining run. "
            "Each run saves a separate candidate and reports RF results on the supplied "
            "evaluation file. Promotion requires a passing non-regression check and explicit "
            "confirmation; the published protocols below are not candidate re-evaluations."
        )
        if model_mode == "Retrained":
            st.warning(
                "The sidebar is set to **Retrained**. Candidate training leaves the selected "
                "artifact unchanged; explicit promotion or rollback changes subsequent scores.",
                icon=":material/warning:",
            )

        feedback_examples = store.feedback_examples()
        cohort_ready = len(feedback_examples) >= MINIMUM_FEEDBACK_EXAMPLES
        st.html(
            ui.tile_row(
                [
                    ui.tile(
                        "ACTIVE MODEL",
                        "retrained" if active_model_path else "baseline-nsl-kdd",
                        "loaded from artifact" if active_model_path else "trained from NSL-KDD",
                        small=True,
                    ),
                    ui.tile(
                        "FEEDBACK EXAMPLES",
                        str(len(feedback_examples)),
                        "false positives corrected",
                        color=ui.TOKENS["status-info"],
                    ),
                    ui.tile(
                        "ARTIFACT",
                        "Available" if selected_artifact else "Not promoted",
                        (
                            "registry-selected"
                            if registry_state["active"]
                            else "legacy artifact" if selected_artifact else "baseline only"
                        ),
                        color=(
                            ui.TOKENS["status-ok"]
                            if selected_artifact
                            else ui.TOKENS["text-tertiary"]
                        ),
                        small=True,
                    ),
                    ui.tile("FORMAT VERSION", str(MODEL_ARTIFACT_FORMAT), "artifact writer schema"),
                ]
            )
        )

        with st.container(key="model_split", gap=18):
            retrain_col, eval_col = st.columns([520, 850], gap="small")

            with (
                retrain_col,
                st.container(key="feedback_retraining", border=True, height="stretch", gap=14),
            ):
                st.html(
                    ui.panel_header(
                        "Feedback retraining",
                        "Corrected rows are appended to the training set with an elevated "
                        "sample weight.",
                    )
                )
                if feedback_examples:
                    cohort = store.list_tickets(review_state="false_positive", limit=6)
                    st.html(
                        ui.section_label("REVIEWED COHORT")
                        + ui.cohort_table(
                            [
                                (
                                    ticket.id,
                                    ticket.event_id,
                                    ticket.predicted_class,
                                    ticket.corrected_class,
                                )
                                for ticket in cohort
                                if ticket.corrected_class is not None
                            ]
                        )
                    )
                else:
                    st.html(
                        ui.callout(
                            "No reviewed cohort yet",
                            f"At least {MINIMUM_FEEDBACK_EXAMPLES} eligible corrections are "
                            "required for a candidate run.",
                            ui.TOKENS["text-tertiary"],
                        )
                    )
                feedback_weight = st.slider(
                    "Feedback sample weight",
                    min_value=1.0,
                    max_value=60.0,
                    value=25.0,
                    step=0.5,
                    format="%.1f",
                    key="feedback_weight",
                    disabled=not feedback_examples,
                )
                if feedback_examples and not cohort_ready:
                    st.warning(
                        f"{len(feedback_examples)} of {MINIMUM_FEEDBACK_EXAMPLES} required eligible corrections.",
                        icon=":material/groups:",
                    )
                st.html(
                    ui.callout(
                        "",
                        "The five-correction minimum and weight limit are prototype safeguards, "
                        "not analyst consensus. Acceptance compares RF accuracy, macro F1 and "
                        "all five class recalls on this evaluation file, not production quality.",
                        ui.TOKENS["status-warn"],
                    )
                )
                with st.container(
                    key="retrain_action", height="stretch", vertical_alignment="bottom"
                ):
                    retrain_clicked = st.button(
                        "Retrain Random Forest",
                        type="primary",
                        icon=":material/model_training:",
                        disabled=not cohort_ready,
                        width="stretch",
                        key="retrain_model",
                    )

            with (
                eval_col,
                st.container(key="evaluation_protocols", border=True, height="stretch", gap=14),
            ):
                st.html(
                    ui.panel_header(
                        "Evaluation protocols",
                        "Published baseline results, reported hardest-first. These are the "
                        "committed figures for the baseline model, not a re-evaluation of any "
                        "retrained candidate.",
                    )
                )
                st.html(
                    "".join(
                        ui.protocol_card(name, dataset, accuracy, macro_f1, colour, blurb)
                        for name, dataset, accuracy, macro_f1, colour, blurb in EVALUATION_PROTOCOLS
                    )
                )

        if retrain_clicked:
            with st.status("Training a separate candidate", expanded=True) as status:
                status.write(f"Loading {len(feedback_examples)} reviewed false-positive event(s)")
                try:
                    registry.create_candidate(
                        database_path=Path(database_value),
                        train_path=dataset_paths.train,
                        test_path=dataset_paths.test,
                        feedback_weight=feedback_weight,
                    )
                    status.update(
                        label="Candidate saved; active model unchanged",
                        state="complete",
                        expanded=False,
                    )
                except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
                    LOGGER.warning("candidate_failed type=%s", type(exc).__name__)
                    status.update(label="Candidate training failed", state="error", expanded=False)
                    st.error(
                        "Candidate training could not complete. Check the eligible cohort, datasets and local storage."
                    )

        candidates = [item for item in registry.candidates() if item["kind"] == "feedback"]
        st.session_state.last_retrain_report = None
        if candidates:
            st.subheader("Candidate selection")
            candidate_id = st.selectbox(
                "Candidate",
                [item["id"] for item in candidates],
                key="candidate_selection",
                format_func=lambda value: next(
                    f"{item['created_at'][:19]} | {'Passed' if item['accepted'] else 'Rejected'} | {value[:8]}"
                    for item in candidates
                    if item["id"] == value
                ),
            )
            candidate = next(item for item in candidates if item["id"] == candidate_id)
            try:
                st.session_state.last_retrain_report = registry.report(candidate_id)
            except (ValueError, OSError, KeyError):
                st.error("Candidate bundle integrity check failed. No model selection was changed.")
                st.stop()
            if candidate["accepted"]:
                st.success(
                    "Evaluation non-regression check passed; not yet production certification."
                )
            else:
                st.warning("Rejected: " + ", ".join(candidate["reasons"]))
            operator = st.text_input("Operator", key="model_operator")
            confirmed = st.checkbox(
                "Confirm active-model change",
                key=f"model_confirm_{candidate_id}_{registry_state['generation']}",
            )
            with st.container(horizontal=True):
                promote_clicked = st.button(
                    "Promote candidate",
                    icon=":material/publish:",
                    key="promote_candidate",
                    disabled=not (
                        confirmed
                        and operator.strip()
                        and candidate["accepted"]
                        and candidate["parent_generation"] == registry_state["generation"]
                    ),
                )
                rollback_clicked = st.button(
                    "Roll back selection",
                    icon=":material/undo:",
                    key="rollback_model",
                    disabled=not (
                        confirmed and operator.strip() and registry_state["generation"] > 0
                    ),
                )
            if promote_clicked or rollback_clicked:
                try:
                    if promote_clicked:
                        registry.promote(
                            candidate_id,
                            expected_generation=registry_state["generation"],
                            actor=operator,
                        )
                    else:
                        registry.rollback(
                            expected_generation=registry_state["generation"], actor=operator
                        )
                    reset_triage_result()
                    st.rerun()
                except (ValueError, OSError, sqlite3.Error) as exc:
                    LOGGER.warning("model_selection_failed type=%s", type(exc).__name__)
                    st.error(
                        "Model selection could not change. Refresh and inspect the candidate/reference integrity."
                    )
            with st.expander("Model selection history", icon=":material/history:"):
                st.json(registry.history())
    if st.session_state.last_retrain_report:
        report = st.session_state.last_retrain_report
        delta = report["updated_macro_f1"] - report["baseline_macro_f1"]
        st.html(ui.section_label("LATEST CANDIDATE"))
        st.html(
            ui.tile_row(
                [
                    ui.tile(
                        "VERSION",
                        report["model_version"][-12:],
                        "full version in report",
                        small=True,
                    ),
                    ui.tile(
                        "CORRECTED BEFORE",
                        f"{report['feedback_corrected_before']}/{report['feedback_examples']}",
                        "baseline model",
                    ),
                    ui.tile(
                        "CORRECTED AFTER",
                        f"{report['feedback_corrected_after']}/{report['feedback_examples']}",
                        "candidate model",
                        color=ui.TOKENS["status-ok"],
                    ),
                    ui.tile(
                        "MACRO F1",
                        f"{report['updated_macro_f1']:.2%}",
                        f"{delta:+.2%} vs baseline",
                        color=(ui.TOKENS["status-ok"] if delta >= 0 else ui.TOKENS["status-warn"]),
                    ),
                ]
            )
        )
        st.download_button(
            "Download retraining report",
            data=json.dumps(report, indent=2),
            file_name="retrain_report.json",
            mime="application/json",
            icon=":material/download:",
        )
