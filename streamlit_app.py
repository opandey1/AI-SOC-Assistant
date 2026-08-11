"""Interactive SOC analyst console for triage, review, and model updates."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from src import ui
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
        evidence_body += ui.evidence_rows(analysis.evidence.get("top_shap_drivers", []))
        st.html(
            ui.card(
                "Why this was flagged",
                "Top SHAP drivers for the predicted class, shown as real observed values.",
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


for key, default_value in {
    "last_analysis": None,
    "last_ticket_id": None,
    "last_retrain_report": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

try:
    dataset_paths = resolve_dataset_paths(search_roots=[PROJECT_ROOT, Path.cwd()])
except FileNotFoundError as exc:
    st.error(str(exc), icon=":material/error:")
    st.stop()

with st.sidebar:
    st.html(ui.section_label("SESSION"))
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

store = FeedbackStore(Path(database_value))
active_model_path = str(DEFAULT_MODEL) if model_mode == "Retrained" else None
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
    width="stretch",
)

if view == "Triage":
    st.header("Connection triage")
    st.caption(
        "Score a single connection, inspect the SHAP drivers behind the verdict, "
        "and generate an analyst ticket."
    )

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
            "Start replay" if input_mode == "Live replay" else "Analyze connection",
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
    else:
        st.html(
            ui.empty_state(
                ui.ICON_SCAN,
                "No connection scored yet",
                "Pick a test row, paste a connection record, or start a live replay. "
                "The verdict, SHAP drivers and generated ticket appear here.",
            )
        )

elif view == "Review queue":
    st.header("Analyst review queue")
    st.caption(
        "Every scored connection is stored in SQLite. Reviews are append-only — "
        "the latest disposition wins, the history is preserved."
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
        table_col, detail_col = st.columns([1.5, 1], gap="medium")
        with table_col:
            selection = st.dataframe(
                queue,
                hide_index=True,
                key="review_queue_table",
                on_select="rerun",
                selection_mode="single-row",
                # Size to content so short queues don't render a block of blank rows.
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

        with detail_col:
            if not selection.selection.rows:
                st.html(
                    ui.empty_state(
                        ui.ICON_QUEUE,
                        "No ticket selected",
                        "Select a row to see its evidence bundle and record a disposition. "
                        "False positives with a corrected class feed the retraining cohort.",
                    )
                )
            else:
                selected = tickets[selection.selection.rows[0]]
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
                            ]
                        ),
                    )
                )

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
                        "Record review",
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

                with st.expander("Incident ticket", icon=":material/article:"):
                    st.markdown(selected.ticket_text)
                with st.expander("Evidence bundle", icon=":material/data_object:"):
                    st.json(selected.evidence)

else:
    st.header("Model operations")
    st.caption(
        "Promote analyst-reviewed false positives into a weighted retraining run, then "
        "compare the candidate against every evaluation protocol before adopting it."
    )

    feedback_examples = store.feedback_examples()
    st.html(
        ui.tile_row(
            [
                ui.tile("ACTIVE MODEL", model_mode, "loaded from artifact", small=True),
                ui.tile(
                    "FEEDBACK EXAMPLES",
                    str(len(feedback_examples)),
                    "false positives corrected",
                    color=ui.TOKENS["status-info"],
                ),
                ui.tile(
                    "ARTIFACT",
                    "Available" if DEFAULT_MODEL.exists() else "Not trained",
                    DEFAULT_MODEL.name,
                    color=(
                        ui.TOKENS["status-ok"]
                        if DEFAULT_MODEL.exists()
                        else ui.TOKENS["text-tertiary"]
                    ),
                    small=True,
                ),
                ui.tile(
                    "REVIEWED",
                    f"{queue_summary['reviewed']:,}",
                    "of {:,} tickets".format(queue_summary["total"]),
                ),
            ]
        )
    )

    retrain_col, eval_col = st.columns([1, 1], gap="medium")

    with retrain_col:
        if feedback_examples:
            cohort_body = ui.section_label("REVIEWED COHORT") + ui.kv_block(
                [
                    (
                        f"#{example.ticket_id} · {example.event_id}",
                        example.corrected_class,
                        ui.family_color(example.corrected_class),
                    )
                    for example in feedback_examples[:6]
                ]
            )
        else:
            cohort_body = ui.callout(
                "No reviewed cohort yet",
                "Mark at least one ticket as a false positive with a corrected class "
                "in the review queue to enable retraining.",
                ui.TOKENS["text-tertiary"],
            )
        cohort_body += '<div style="height:12px"></div>' + ui.callout(
            "Promotion is not automatic",
            "A single correction changes the intended row but can reduce aggregate "
            "cross-distribution accuracy. Production promotion needs a larger cohort "
            "and a held-out acceptance gate.",
            ui.TOKENS["status-warn"],
        )
        st.html(
            ui.card(
                "Feedback retraining",
                "Corrected rows are appended to the training set with an elevated sample weight.",
                cohort_body,
            )
        )

        retrain_clicked = st.button(
            "Retrain Random Forest",
            type="primary",
            icon=":material/model_training:",
            disabled=not feedback_examples,
            width="stretch",
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

    with eval_col:
        st.html(
            ui.card(
                "Evaluation protocols",
                "Three independent protocols, reported hardest-first.",
                "".join(
                    ui.protocol_card(name, dataset, accuracy, macro_f1, colour, blurb)
                    for name, dataset, accuracy, macro_f1, colour, blurb in EVALUATION_PROTOCOLS
                ),
            )
        )

    if st.session_state.last_retrain_report is None and DEFAULT_RETRAIN_REPORT.exists():
        st.session_state.last_retrain_report = json.loads(
            DEFAULT_RETRAIN_REPORT.read_text(encoding="utf-8")
        )
    if st.session_state.last_retrain_report:
        report = st.session_state.last_retrain_report
        delta = report["updated_macro_f1"] - report["baseline_macro_f1"]
        st.html(ui.section_label("LATEST CANDIDATE"))
        st.html(
            ui.tile_row(
                [
                    ui.tile("VERSION", report["model_version"], "atomic write", small=True),
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
