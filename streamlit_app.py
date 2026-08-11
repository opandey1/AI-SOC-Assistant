"""Interactive SOC analyst console for triage, review, and model updates."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from src.feedback import CORRECTABLE_CLASSES, REVIEW_DISPOSITIONS, FeedbackStore
from src.ingest import MODEL_INPUT_COLUMNS, NSL_KDD_COLUMNS, load_nsl_kdd, resolve_dataset_paths
from src.model_store import load_model_artifact, runtime_from_artifact
from src.retrain import retrain_from_feedback, save_report
from src.runtime import (
    ConnectionAnalysis,
    analyze_raw_connection,
    build_runtime,
    jsonable_record,
)
from src.streaming import ConnectionEvent, replay_events

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = PROJECT_ROOT / "state" / "soc_feedback.db"
DEFAULT_MODEL = PROJECT_ROOT / "models" / "soc_model.joblib"
DEFAULT_RETRAIN_REPORT = PROJECT_ROOT / "state" / "retrain_report.json"

st.set_page_config(
    page_title="SOC analyst console",
    page_icon=":material/security:",
    layout="wide",
)


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
    badge_color = "red" if analysis.score.fused_anomaly else "green"
    badge_icon = ":material/warning:" if analysis.score.fused_anomaly else ":material/check_circle:"
    st.badge(analysis.verdict.upper(), color=badge_color, icon=badge_icon)

    with st.container(horizontal=True):
        st.metric("Classification", analysis.predicted_class.upper(), border=True)
        st.metric(
            "Fused confidence", analysis.score.fused_confidence, format="percent", border=True
        )
        st.metric("RF confidence", analysis.score.rf_confidence, format="percent", border=True)
        st.metric("Isolation risk", analysis.score.isolation_risk, format="percent", border=True)

    st.subheader("SHAP evidence")
    drivers = pd.DataFrame(analysis.evidence.get("top_shap_drivers", []))
    if drivers.empty:
        st.caption("No SHAP drivers were generated for this verdict.")
    else:
        drivers = drivers.rename(
            columns={
                "feature": "Feature",
                "true_value": "Observed value",
                "shap_value": "SHAP contribution",
                "direction": "Direction",
            }
        )
        st.bar_chart(
            drivers,
            x="Feature",
            y="SHAP contribution",
            color="Direction",
            horizontal=True,
            height=330,
        )
        st.dataframe(
            drivers,
            hide_index=True,
            column_config={
                "SHAP contribution": st.column_config.NumberColumn(format="%.5f"),
            },
        )

    st.subheader("Incident ticket")
    if analysis.ticket:
        if ticket_id is not None:
            st.caption(f"Stored as review ticket #{ticket_id}")
        st.markdown(analysis.ticket)
    else:
        st.success("Normal verdict. No incident ticket generated.", icon=":material/check_circle:")

    evidence_expander = st.expander(
        "Scoring details",
        icon=":material/data_object:",
        on_change="rerun",
    )
    if evidence_expander.open:
        with evidence_expander:
            st.json(analysis.evidence)


for key, default_value in {
    "last_analysis": None,
    "last_ticket_id": None,
    "last_retrain_report": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

st.title("SOC analyst console")
st.caption("AI-SOC-Assistant / detection, explanation, review")

try:
    dataset_paths = resolve_dataset_paths(search_roots=[PROJECT_ROOT, Path.cwd()])
except FileNotFoundError as exc:
    st.error(str(exc), icon=":material/error:")
    st.stop()

with st.sidebar:
    st.subheader("Session")
    model_options = ["Baseline"]
    if DEFAULT_MODEL.exists():
        model_options.append("Retrained")
    model_mode = st.segmented_control(
        "Model",
        model_options,
        default=model_options[-1],
        width="stretch",
    )
    provider = st.selectbox(
        "Ticket provider",
        ["template", "ollama", "openai", "anthropic"],
        index=0,
    )
    database_value = st.text_input("Review database", value=str(DEFAULT_DATABASE))
    st.caption(f"Train rows: {count_rows(str(dataset_paths.train)):,}")
    st.caption(f"Test file: {dataset_paths.test.name}")

store = FeedbackStore(Path(database_value))
active_model_path = str(DEFAULT_MODEL) if model_mode == "Retrained" else None
view = st.segmented_control(
    "Workspace",
    ["Triage", "Review queue", "Model"],
    default="Triage",
    width="stretch",
)

if view == "Triage":
    st.header("Connection triage")
    input_mode = st.segmented_control(
        "Input",
        ["Dataset row", "JSON record", "Live replay"],
        default="Dataset row",
    )
    source_ip = "192.0.2.47"
    row_index = 0
    replay_count = 5
    replay_delay = 0.5
    raw_json = ""

    with st.form("triage_form"):
        source_ip = st.text_input("Source IP", value=source_ip)
        if input_mode == "Dataset row":
            row_index = int(st.number_input("Test row", min_value=0, value=0, step=1))
        elif input_mode == "JSON record":
            default_record = load_raw_test_row(str(dataset_paths.test), 0)
            raw_json = st.text_area(
                "Connection JSON",
                value=json.dumps(default_record, indent=2),
                height=320,
            )
        else:
            with st.container(horizontal=True):
                row_index = int(st.number_input("Start row", min_value=0, value=0, step=1))
                replay_count = int(
                    st.number_input("Events", min_value=1, max_value=20, value=5, step=1)
                )
                replay_delay = float(
                    st.number_input(
                        "Interval (seconds)",
                        min_value=0.0,
                        max_value=5.0,
                        value=0.5,
                        step=0.1,
                    )
                )
        submitted = st.form_submit_button(
            "Start replay" if input_mode == "Live replay" else "Analyze",
            type="primary",
            icon=":material/play_arrow:",
        )

    if submitted:
        runtime_slot = st.container()
        with runtime_slot, st.status("Loading detection runtime", expanded=True) as status:
            runtime = get_runtime(
                str(dataset_paths.train),
                str(dataset_paths.test),
                active_model_path,
            )
            status.write(f"Active model: {runtime.model_version}")

            if input_mode == "Live replay":
                feed_slot = st.empty()
                feed_rows = []
                last_analysis = None
                last_ticket_id = None
                for event in replay_events(
                    runtime.dataset.test,
                    start_index=row_index,
                    limit=replay_count,
                    delay=replay_delay,
                ):
                    analysis = analyze_raw_connection(
                        event.record,
                        runtime=runtime,
                        source_ip=event.source_ip,
                        provider=provider,
                    )
                    ticket_id = persist_alert(store, event, analysis)
                    feed_rows.append(
                        {
                            "Event": event.event_id,
                            "Source IP": event.source_ip,
                            "Class": analysis.predicted_class,
                            "Confidence": analysis.score.fused_confidence,
                            "Verdict": analysis.verdict,
                        }
                    )
                    feed_slot.dataframe(
                        pd.DataFrame(feed_rows),
                        hide_index=True,
                        column_config={
                            "Confidence": st.column_config.ProgressColumn(
                                min_value=0.0,
                                max_value=1.0,
                                format="percent",
                            )
                        },
                    )
                    status.write(
                        f"{event.event_id}: {analysis.predicted_class} / {analysis.verdict}"
                    )
                    last_analysis = analysis
                    last_ticket_id = ticket_id
                st.session_state.last_analysis = last_analysis
                st.session_state.last_ticket_id = last_ticket_id
            else:
                if input_mode == "JSON record":
                    try:
                        record = json.loads(raw_json)
                    except json.JSONDecodeError as exc:
                        status.update(label="Invalid connection JSON", state="error")
                        st.error(str(exc), icon=":material/error:")
                        st.stop()
                    if not isinstance(record, dict):
                        status.update(label="Invalid connection JSON", state="error")
                        st.error("Connection JSON must be an object.", icon=":material/error:")
                        st.stop()
                    event_id = f"dashboard-json-{uuid4()}"
                    event_source = "dashboard-json"
                else:
                    record = load_raw_test_row(str(dataset_paths.test), row_index)
                    event_id = f"dashboard-row-{row_index}-{uuid4()}"
                    event_source = "dashboard-row"
                event = ConnectionEvent(
                    event_id=event_id,
                    observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    source_ip=source_ip,
                    source=event_source,
                    record=record,
                )
                analysis = analyze_raw_connection(
                    event.record,
                    runtime=runtime,
                    source_ip=event.source_ip,
                    provider=provider,
                )
                st.session_state.last_ticket_id = persist_alert(store, event, analysis)
                st.session_state.last_analysis = analysis
            status.update(label="Analysis complete", state="complete", expanded=False)

    if st.session_state.last_analysis is not None:
        show_analysis(st.session_state.last_analysis, st.session_state.last_ticket_id)

elif view == "Review queue":
    st.header("Analyst review queue")
    queue_summary = store.summary()
    with st.container(horizontal=True):
        st.metric("Tickets", queue_summary["total"], border=True)
        st.metric("Unreviewed", queue_summary["unreviewed"], border=True)
        st.metric("Reviewed", queue_summary["reviewed"], border=True)
        st.metric("False positives", queue_summary["false_positives"], border=True)

    review_state = st.segmented_control(
        "Review state",
        ["all", "unreviewed", *REVIEW_DISPOSITIONS],
        default="unreviewed",
        format_func=lambda value: value.replace("_", " ").capitalize(),
    )
    tickets = store.list_tickets(review_state=review_state, limit=200)
    if not tickets:
        st.info("No tickets match this review state.", icon=":material/inbox:")
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
            key="review_queue_table",
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "ID": st.column_config.NumberColumn(format="#%d", pinned=True),
                "Confidence": st.column_config.ProgressColumn(
                    min_value=0.0,
                    max_value=1.0,
                    format="percent",
                ),
            },
        )
        if selection.selection.rows:
            selected = tickets[selection.selection.rows[0]]
            st.subheader(f"Ticket #{selected.id}")
            with st.form("review_form"):
                disposition = st.selectbox(
                    "Disposition",
                    REVIEW_DISPOSITIONS,
                    format_func=lambda value: value.replace("_", " ").capitalize(),
                )
                corrected_class = st.selectbox(
                    "Corrected class",
                    CORRECTABLE_CLASSES,
                    index=CORRECTABLE_CLASSES.index("normal"),
                )
                analyst = st.text_input("Analyst", value="analyst")
                notes = st.text_area("Notes")
                review_submitted = st.form_submit_button(
                    "Save review",
                    type="primary",
                    icon=":material/save:",
                )
            if review_submitted:
                store.record_review(
                    selected.id,
                    disposition=disposition,
                    corrected_class=corrected_class,
                    analyst_notes=notes,
                    reviewed_by=analyst,
                )
                st.toast("Review saved", icon=":material/check_circle:")
                st.rerun()

            ticket_expander = st.expander(
                "Incident ticket",
                icon=":material/article:",
                on_change="rerun",
            )
            if ticket_expander.open:
                with ticket_expander:
                    st.markdown(selected.ticket_text)
            evidence_expander = st.expander(
                "Evidence bundle",
                icon=":material/data_object:",
                on_change="rerun",
            )
            if evidence_expander.open:
                with evidence_expander:
                    st.json(selected.evidence)

else:
    st.header("Model operations")
    feedback_examples = store.feedback_examples()
    with st.container(horizontal=True):
        st.metric("Active model", model_mode, border=True)
        st.metric("Feedback examples", len(feedback_examples), border=True)
        st.metric("Artifact", "Available" if DEFAULT_MODEL.exists() else "Not trained", border=True)

    st.caption(str(DEFAULT_MODEL))
    retrain_clicked = st.button(
        "Retrain Random Forest",
        type="primary",
        icon=":material/model_training:",
        disabled=not feedback_examples,
    )
    if retrain_clicked:
        with st.status("Applying analyst feedback", expanded=True) as status:
            status.write(f"Loading {len(feedback_examples)} reviewed false-positive event(s)")
            report = retrain_from_feedback(
                database_path=Path(database_value),
                output_model=DEFAULT_MODEL,
                train_path=dataset_paths.train,
                test_path=dataset_paths.test,
            )
            status.write("Saving versioned model artifact")
            save_report(report, DEFAULT_RETRAIN_REPORT)
            st.session_state.last_retrain_report = asdict(report)
            get_runtime.clear()
            status.update(label="Random Forest updated", state="complete", expanded=False)
        st.toast("Retrained model is ready", icon=":material/check_circle:")

    if st.session_state.last_retrain_report is None and DEFAULT_RETRAIN_REPORT.exists():
        st.session_state.last_retrain_report = json.loads(
            DEFAULT_RETRAIN_REPORT.read_text(encoding="utf-8")
        )
    if st.session_state.last_retrain_report:
        report = st.session_state.last_retrain_report
        st.subheader("Latest update")
        with st.container(horizontal=True):
            st.metric("Version", report["model_version"], border=True)
            st.metric(
                "Corrected before",
                f"{report['feedback_corrected_before']}/{report['feedback_examples']}",
                border=True,
            )
            st.metric(
                "Corrected after",
                f"{report['feedback_corrected_after']}/{report['feedback_examples']}",
                border=True,
            )
            st.metric(
                "Macro F1",
                report["updated_macro_f1"],
                delta=report["updated_macro_f1"] - report["baseline_macro_f1"],
                format="percent",
                border=True,
            )
        st.download_button(
            "Download retraining report",
            data=json.dumps(report, indent=2),
            file_name="retrain_report.json",
            mime="application/json",
            icon=":material/download:",
        )
