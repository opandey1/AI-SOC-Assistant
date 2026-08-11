"""Tests for transport-neutral replay and Kafka publishing contracts."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

import src.streaming
from src.ingest import CATEGORICAL_FEATURES, MODEL_INPUT_COLUMNS
from src.runtime import ConnectionAnalysis
from src.streaming import (
    ConnectionEvent,
    build_parser,
    event_from_payload,
    event_payload,
    process_event_stream,
    publish_events,
    replay_events,
)
from src.train import ConnectionScore


def _record(seed: int = 0):
    record = {column: seed for column in MODEL_INPUT_COLUMNS}
    record.update({"protocol_type": "tcp", "service": "http", "flag": "SF"})
    return record


def _analysis(*, alert: bool):
    score = ConnectionScore(
        rf_prediction=1 if alert else 0,
        rf_confidence=0.9,
        rf_anomaly_confidence=0.9 if alert else 0.1,
        isolation_score=-0.1,
        isolation_risk=0.8 if alert else 0.1,
        rf_anomaly=alert,
        isolation_anomaly=False,
        fused_anomaly=alert,
        fused_confidence=0.7 if alert else 0.1,
        alert_reason="random_forest" if alert else "none",
    )
    return ConnectionAnalysis(
        source_ip="192.0.2.1",
        raw_record=_record(),
        processed_record={"feature": 1.0},
        score=score,
        evidence={"predicted_class": "dos" if alert else "normal"},
        ticket="ticket" if alert else None,
        model_version="test",
    )


def test_event_payload_round_trip_and_required_feature_validation():
    event = ConnectionEvent(
        event_id="event-7",
        observed_at="2026-08-11T10:00:00+00:00",
        source_ip="192.0.2.7",
        source="unit-test",
        record=_record(7),
    )

    restored = event_from_payload(event_payload(event))

    assert restored == event
    with pytest.raises(ValueError, match="missing required"):
        event_from_payload({"duration": 1})


def test_replay_yields_rows_sequentially_and_delays_between_events():
    rows = pd.DataFrame([_record(1), _record(2)])
    sleeps = []

    events = list(replay_events(rows, delay=0.25, sleep=sleeps.append))

    assert [event.event_id for event in events] == ["nsl-kdd-000000", "nsl-kdd-000001"]
    assert sleeps == [0.25]
    assert all(set(event.record) == set(MODEL_INPUT_COLUMNS) for event in events)
    assert all(event.record[column] == "tcp" for event in events for column in ["protocol_type"])
    assert set(CATEGORICAL_FEATURES) <= set(events[0].record)


def test_publish_uses_keyed_json_and_waits_for_delivery():
    class FakeProducer:
        def __init__(self, config):
            self.config = config
            self.messages = []

        def produce(self, topic, *, key, value, on_delivery):
            self.messages.append((topic, key, json.loads(value)))
            on_delivery(None, object())

        def poll(self, _timeout):
            return 0

        def flush(self, _timeout):
            return 0

    producers = []

    def factory(config):
        producer = FakeProducer(config)
        producers.append(producer)
        return producer

    event = ConnectionEvent("event-1", "now", "192.0.2.1", "test", _record())

    assert (
        publish_events(
            [event],
            bootstrap_servers="broker:9092",
            topic="connections",
            producer_factory=factory,
        )
        == 1
    )
    assert producers[0].messages[0][1] == b"event-1"
    assert producers[0].messages[0][2]["connection"]["service"] == "http"


def test_process_stream_persists_alerts_only(monkeypatch):
    analyses = iter([_analysis(alert=False), _analysis(alert=True)])
    monkeypatch.setattr(
        src.streaming, "analyze_raw_connection", lambda *args, **kwargs: next(analyses)
    )

    class Store:
        def __init__(self):
            self.events = []

        def log_analysis(self, analysis, **metadata):
            self.events.append((analysis, metadata))

    store = Store()
    events = [
        ConnectionEvent("normal", "now", "192.0.2.1", "test", _record()),
        ConnectionEvent("alert", "now", "192.0.2.2", "test", _record()),
    ]

    summary = process_event_stream(
        events,
        runtime=SimpleNamespace(),
        store=store,
    )

    assert summary.processed == 2
    assert summary.alerts == 1
    assert summary.normals == 1
    assert summary.stored_tickets == 1
    assert store.events[0][1]["event_id"] == "alert"


@pytest.mark.parametrize("value", ["-0.1", "1.1", "nan", "inf"])
def test_stream_cli_rejects_invalid_isolation_threshold(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["replay", "--isolation-threshold", value])
