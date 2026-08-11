"""SQLite-backed incident tickets and append-only analyst feedback."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingest import CLASS_NAMES
from src.runtime import ConnectionAnalysis

REVIEW_DISPOSITIONS = ("confirmed_attack", "false_positive", "needs_investigation")
CORRECTABLE_CLASSES = tuple(name for name in CLASS_NAMES if name != "unknown")


@dataclass(frozen=True)
class TicketRecord:
    """One stored ticket joined to its most recent analyst review."""

    id: int
    event_id: str
    observed_at: str
    created_at: str
    source: str
    source_ip: str
    predicted_class: str
    rf_confidence: float
    fused_confidence: float
    alert_reason: str
    raw_record: dict[str, Any]
    evidence: dict[str, Any]
    ticket_text: str
    model_version: str
    disposition: str | None
    corrected_class: str | None
    analyst_notes: str | None
    reviewed_by: str | None
    reviewed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeedbackExample:
    """A reviewed raw record and its analyst-corrected target."""

    ticket_id: int
    event_id: str
    raw_record: dict[str, Any]
    corrected_class: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FeedbackStore:
    """Small, concurrency-friendly SQLite repository for analyst decisions."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_ip TEXT NOT NULL,
                    predicted_class TEXT NOT NULL,
                    rf_confidence REAL NOT NULL,
                    fused_confidence REAL NOT NULL,
                    alert_reason TEXT NOT NULL,
                    raw_record_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    ticket_text TEXT NOT NULL,
                    model_version TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    disposition TEXT NOT NULL CHECK (
                        disposition IN (
                            'confirmed_attack',
                            'false_positive',
                            'needs_investigation'
                        )
                    ),
                    corrected_class TEXT,
                    analyst_notes TEXT,
                    reviewed_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_tickets_created_at
                    ON tickets(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_reviews_ticket_id
                    ON reviews(ticket_id, id DESC);
                """
            )

    def log_analysis(
        self,
        analysis: ConnectionAnalysis,
        *,
        event_id: str,
        source: str,
        observed_at: str | None = None,
    ) -> int:
        """Persist one generated alert ticket, idempotently by event ID."""

        if not analysis.ticket or not analysis.score.fused_anomaly:
            raise ValueError("Only analyses with generated alert tickets can be stored.")

        created_at = _utc_now()
        values = (
            str(event_id),
            observed_at or created_at,
            created_at,
            str(source),
            analysis.source_ip,
            analysis.predicted_class,
            analysis.score.rf_confidence,
            analysis.score.fused_confidence,
            analysis.score.alert_reason,
            json.dumps(analysis.raw_record, sort_keys=True, allow_nan=False),
            json.dumps(analysis.evidence, sort_keys=True, allow_nan=False),
            analysis.ticket,
            analysis.model_version,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tickets (
                    event_id, observed_at, created_at, source, source_ip,
                    predicted_class, rf_confidence, fused_confidence,
                    alert_reason, raw_record_json, evidence_json,
                    ticket_text, model_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                values,
            )
            row = connection.execute(
                "SELECT id FROM tickets WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()
        if row is None:
            raise RuntimeError("Ticket insert did not return a stored row.")
        return int(row["id"])

    def record_review(
        self,
        ticket_id: int,
        *,
        disposition: str,
        corrected_class: str | None = None,
        analyst_notes: str | None = None,
        reviewed_by: str = "analyst",
    ) -> int:
        """Append a review while preserving earlier decisions for auditability."""

        if disposition not in REVIEW_DISPOSITIONS:
            raise ValueError(f"Unsupported disposition: {disposition}")
        if disposition == "false_positive" and corrected_class is None:
            corrected_class = "normal"
        if corrected_class is not None and corrected_class not in CORRECTABLE_CLASSES:
            raise ValueError(f"Unsupported corrected class: {corrected_class}")
        if not reviewed_by.strip():
            raise ValueError("reviewed_by must not be empty.")

        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM tickets WHERE id = ?",
                (ticket_id,),
            ).fetchone()
            if exists is None:
                raise KeyError(f"Ticket {ticket_id} does not exist.")
            cursor = connection.execute(
                """
                INSERT INTO reviews (
                    ticket_id, disposition, corrected_class,
                    analyst_notes, reviewed_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id,
                    disposition,
                    corrected_class,
                    analyst_notes.strip() if analyst_notes else None,
                    reviewed_by.strip(),
                    _utc_now(),
                ),
            )
            review_id = cursor.lastrowid
        if review_id is None:
            raise RuntimeError("Review insert did not return an ID.")
        return int(review_id)

    @staticmethod
    def _ticket_from_row(row: sqlite3.Row) -> TicketRecord:
        return TicketRecord(
            id=int(row["id"]),
            event_id=str(row["event_id"]),
            observed_at=str(row["observed_at"]),
            created_at=str(row["created_at"]),
            source=str(row["source"]),
            source_ip=str(row["source_ip"]),
            predicted_class=str(row["predicted_class"]),
            rf_confidence=float(row["rf_confidence"]),
            fused_confidence=float(row["fused_confidence"]),
            alert_reason=str(row["alert_reason"]),
            raw_record=json.loads(row["raw_record_json"]),
            evidence=json.loads(row["evidence_json"]),
            ticket_text=str(row["ticket_text"]),
            model_version=str(row["model_version"]),
            disposition=row["disposition"],
            corrected_class=row["corrected_class"],
            analyst_notes=row["analyst_notes"],
            reviewed_by=row["reviewed_by"],
            reviewed_at=row["reviewed_at"],
        )

    @staticmethod
    def _select_with_latest_review() -> str:
        return """
            SELECT
                t.*,
                r.disposition,
                r.corrected_class,
                r.analyst_notes,
                r.reviewed_by,
                r.created_at AS reviewed_at
            FROM tickets AS t
            LEFT JOIN reviews AS r ON r.id = (
                SELECT latest.id
                FROM reviews AS latest
                WHERE latest.ticket_id = t.id
                ORDER BY latest.id DESC
                LIMIT 1
            )
        """

    def get_ticket(self, ticket_id: int) -> TicketRecord:
        with self._connect() as connection:
            row = connection.execute(
                self._select_with_latest_review() + " WHERE t.id = ?",
                (ticket_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Ticket {ticket_id} does not exist.")
        return self._ticket_from_row(row)

    def list_tickets(
        self,
        *,
        review_state: str = "all",
        limit: int = 200,
    ) -> list[TicketRecord]:
        """Return newest tickets, optionally filtered by review state."""

        if review_state not in {"all", "unreviewed", *REVIEW_DISPOSITIONS}:
            raise ValueError(f"Unsupported review state: {review_state}")
        if limit <= 0:
            raise ValueError("limit must be greater than 0.")

        query = self._select_with_latest_review()
        parameters: list[Any] = []
        if review_state == "unreviewed":
            query += " WHERE r.id IS NULL"
        elif review_state != "all":
            query += " WHERE r.disposition = ?"
            parameters.append(review_state)
        query += " ORDER BY t.created_at DESC, t.id DESC LIMIT ?"
        parameters.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._ticket_from_row(row) for row in rows]

    def feedback_examples(self) -> list[FeedbackExample]:
        """Return latest false-positive corrections for model retraining."""

        query = (
            self._select_with_latest_review()
            + """
            WHERE r.disposition = 'false_positive'
              AND r.corrected_class IS NOT NULL
            ORDER BY t.id
        """
        )
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [
            FeedbackExample(
                ticket_id=int(row["id"]),
                event_id=str(row["event_id"]),
                raw_record=json.loads(row["raw_record_json"]),
                corrected_class=str(row["corrected_class"]),
            )
            for row in rows
        ]

    def summary(self) -> dict[str, int]:
        with self._connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
            reviewed = int(
                connection.execute("SELECT COUNT(DISTINCT ticket_id) FROM reviews").fetchone()[0]
            )
            false_positives = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM tickets AS t
                    JOIN reviews AS r ON r.id = (
                        SELECT latest.id
                        FROM reviews AS latest
                        WHERE latest.ticket_id = t.id
                        ORDER BY latest.id DESC
                        LIMIT 1
                    )
                    WHERE r.disposition = 'false_positive'
                    """
                ).fetchone()[0]
            )
        return {
            "total": total,
            "reviewed": reviewed,
            "unreviewed": total - reviewed,
            "false_positives": false_positives,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and review stored SOC tickets.")
    parser.add_argument("--database", type=Path, default=Path("state") / "soc_feedback.db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List stored tickets.")
    list_parser.add_argument(
        "--state",
        default="all",
        choices=["all", "unreviewed", *REVIEW_DISPOSITIONS],
    )
    list_parser.add_argument("--limit", type=int, default=20)

    review = subparsers.add_parser("review", help="Append an analyst decision.")
    review.add_argument("ticket_id", type=int)
    review.add_argument("--disposition", required=True, choices=REVIEW_DISPOSITIONS)
    review.add_argument("--corrected-class", choices=CORRECTABLE_CLASSES)
    review.add_argument("--notes")
    review.add_argument("--analyst", default="analyst")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    store = FeedbackStore(args.database)
    if args.command == "review":
        review_id = store.record_review(
            args.ticket_id,
            disposition=args.disposition,
            corrected_class=args.corrected_class,
            analyst_notes=args.notes,
            reviewed_by=args.analyst,
        )
        print(f"Stored review {review_id} for ticket {args.ticket_id}.")
        return

    tickets = store.list_tickets(review_state=args.state, limit=args.limit)
    if not tickets:
        print("No tickets matched the requested review state.")
        return
    for ticket in tickets:
        state = ticket.disposition or "unreviewed"
        print(
            f"#{ticket.id} {ticket.observed_at} {ticket.source_ip} "
            f"{ticket.predicted_class} {ticket.fused_confidence:.1%} [{state}]"
        )


if __name__ == "__main__":
    main()
