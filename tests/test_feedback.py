"""Tests for the SQLite ticket and analyst-review repository."""

from pathlib import Path

import pytest

from src.feedback import FeedbackStore
from src.runtime import ConnectionAnalysis
from src.train import ConnectionScore


def _analysis(*, alert: bool = True) -> ConnectionAnalysis:
    score = ConnectionScore(
        rf_prediction=1 if alert else 0,
        rf_confidence=0.91,
        rf_anomaly_confidence=0.91 if alert else 0.03,
        isolation_score=-0.2,
        isolation_risk=0.8 if alert else 0.1,
        rf_anomaly=alert,
        isolation_anomaly=alert,
        fused_anomaly=alert,
        fused_confidence=0.86 if alert else 0.06,
        alert_reason="both" if alert else "none",
    )
    return ConnectionAnalysis(
        source_ip="192.0.2.7",
        raw_record={"duration": 4, "protocol_type": "tcp"},
        processed_record={"duration": 4.0},
        score=score,
        evidence={"predicted_class": "dos" if alert else "normal"},
        ticket="incident ticket" if alert else None,
        model_version="test-model",
    )


def test_ticket_logging_is_idempotent_and_review_history_is_append_only(tmp_path: Path):
    store = FeedbackStore(tmp_path / "feedback.db")

    first_id = store.log_analysis(
        _analysis(),
        event_id="event-1",
        source="unit-test",
        observed_at="2026-08-11T10:00:00+00:00",
    )
    duplicate_id = store.log_analysis(
        _analysis(),
        event_id="event-1",
        source="unit-test",
    )

    assert duplicate_id == first_id
    assert store.summary() == {
        "total": 1,
        "reviewed": 0,
        "unreviewed": 1,
        "false_positives": 0,
    }

    first_review = store.record_review(
        first_id,
        disposition="false_positive",
        analyst_notes="Known scanner maintenance window",
        reviewed_by="alice",
    )
    reviewed = store.get_ticket(first_id)

    assert first_review > 0
    assert reviewed.disposition == "false_positive"
    assert reviewed.corrected_class == "normal"
    assert reviewed.reviewed_by == "alice"
    assert store.feedback_examples()[0].event_id == "event-1"

    second_review = store.record_review(
        first_id,
        disposition="needs_investigation",
        reviewed_by="bob",
    )

    assert second_review > first_review
    assert store.get_ticket(first_id).disposition == "needs_investigation"
    assert store.feedback_examples() == []


def test_store_rejects_normal_verdicts_and_invalid_reviews(tmp_path: Path):
    store = FeedbackStore(tmp_path / "feedback.db")

    with pytest.raises(ValueError, match="generated alert tickets"):
        store.log_analysis(_analysis(alert=False), event_id="normal-1", source="unit-test")
    with pytest.raises(KeyError, match="does not exist"):
        store.record_review(999, disposition="false_positive")
    with pytest.raises(ValueError, match="Unsupported disposition"):
        store.record_review(999, disposition="ignored")
