"""Delayed replay and Kafka-compatible streaming for live SOC triage."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from src.ingest import MODEL_INPUT_COLUMNS
from src.runtime import AnalysisRuntime, ConnectionAnalysis, analyze_raw_connection, jsonable_record

DEFAULT_TOPIC = "soc.connections"
DEFAULT_BOOTSTRAP_SERVERS = "localhost:19092"
DEFAULT_DATABASE = Path("state") / "soc_feedback.db"


@dataclass(frozen=True)
class ConnectionEvent:
    """Transport-neutral connection event consumed by the analysis runtime."""

    event_id: str
    observed_at: str
    source_ip: str
    source: str
    record: dict[str, Any]


@dataclass(frozen=True)
class StreamSummary:
    """Counters returned after a finite stream completes."""

    processed: int
    alerts: int
    normals: int
    stored_tickets: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("value must be a finite number greater than or equal to 0")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def _unit_interval(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("value must be a finite number between 0 and 1")
    return parsed


def event_from_payload(payload: Mapping[str, Any], *, source: str = "kafka") -> ConnectionEvent:
    """Validate an event envelope or a direct raw-record JSON object."""

    envelope = dict(payload)
    record_value = envelope.get("connection", envelope.get("record"))
    if record_value is None:
        record_value = {
            column: envelope[column] for column in MODEL_INPUT_COLUMNS if column in envelope
        }
    if not isinstance(record_value, Mapping):
        raise ValueError("Event field 'connection' must be a JSON object.")

    record = jsonable_record(record_value)
    missing = [column for column in MODEL_INPUT_COLUMNS if column not in record]
    if missing:
        raise ValueError(f"Event is missing required connection fields: {', '.join(missing)}")

    return ConnectionEvent(
        event_id=str(envelope.get("event_id") or uuid4()),
        observed_at=str(envelope.get("observed_at") or _utc_now()),
        source_ip=str(envelope.get("source_ip") or "unknown"),
        source=str(envelope.get("source") or source),
        record={column: record[column] for column in MODEL_INPUT_COLUMNS},
    )


def event_payload(event: ConnectionEvent) -> dict[str, Any]:
    """Return the documented Kafka JSON envelope for one event."""

    return {
        "event_id": event.event_id,
        "observed_at": event.observed_at,
        "source_ip": event.source_ip,
        "source": event.source,
        "connection": event.record,
    }


def replay_events(
    rows: pd.DataFrame,
    *,
    start_index: int = 0,
    limit: int | None = None,
    delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[ConnectionEvent]:
    """Yield raw rows one at a time with a configurable inter-event delay."""

    if start_index < 0 or start_index >= len(rows):
        raise IndexError("start-index is outside the available replay rows.")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than 0 when supplied.")
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("delay must be a finite value greater than or equal to 0.")

    stop_index = len(rows) if limit is None else min(len(rows), start_index + limit)
    for offset, row_index in enumerate(range(start_index, stop_index)):
        if offset and delay:
            sleep(delay)
        row = rows.iloc[row_index]
        row_record = jsonable_record(row)
        yield ConnectionEvent(
            event_id=f"nsl-kdd-{row_index:06d}",
            observed_at=_utc_now(),
            source_ip=f"192.0.2.{(row_index % 253) + 1}",
            source="nsl-kdd-replay",
            record={column: row_record[column] for column in MODEL_INPUT_COLUMNS},
        )


def kafka_events(
    *,
    bootstrap_servers: str,
    topic: str,
    group_id: str,
    limit: int | None = None,
    idle_timeout: float | None = None,
    consumer_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> Iterator[ConnectionEvent]:
    """Consume JSON connection events from Kafka or a Kafka-compatible broker."""

    if consumer_factory is None:
        try:
            from confluent_kafka import Consumer
        except ImportError as exc:
            raise RuntimeError(
                "Kafka mode requires confluent-kafka. Install the pinned requirements."
            ) from exc
        consumer_factory = Consumer

    consumer = consumer_factory(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        }
    )
    consumer.subscribe([topic])
    consumed = 0
    last_message_at = time.monotonic()
    try:
        while limit is None or consumed < limit:
            message = consumer.poll(1.0)
            if message is None:
                if idle_timeout is not None and time.monotonic() - last_message_at >= idle_timeout:
                    break
                continue
            if message.error():
                raise RuntimeError(f"Kafka consumer error: {message.error()}")
            last_message_at = time.monotonic()
            try:
                payload = json.loads(message.value().decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Kafka event value must be a UTF-8 JSON object.") from exc
            if not isinstance(payload, dict):
                raise ValueError("Kafka event value must decode to a JSON object.")
            yield event_from_payload(payload)
            consumed += 1
    finally:
        consumer.close()


def publish_events(
    events: Iterable[ConnectionEvent],
    *,
    bootstrap_servers: str,
    topic: str,
    producer_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> int:
    """Publish replay events as keyed JSON messages and wait for delivery."""

    if producer_factory is None:
        try:
            from confluent_kafka import Producer
        except ImportError as exc:
            raise RuntimeError(
                "Kafka mode requires confluent-kafka. Install the pinned requirements."
            ) from exc
        producer_factory = Producer

    producer = producer_factory({"bootstrap.servers": bootstrap_servers})
    delivery_errors: list[str] = []

    def delivered(error: Any, _message: Any) -> None:
        if error is not None:
            delivery_errors.append(str(error))

    published = 0
    for event in events:
        while True:
            try:
                producer.produce(
                    topic,
                    key=event.event_id.encode("utf-8"),
                    value=json.dumps(event_payload(event), sort_keys=True).encode("utf-8"),
                    on_delivery=delivered,
                )
                break
            except BufferError:
                producer.poll(1.0)
        producer.poll(0)
        published += 1

    undelivered = producer.flush(15.0)
    if undelivered or delivery_errors:
        details = "; ".join(delivery_errors) or f"{undelivered} message(s) undelivered"
        raise RuntimeError(f"Kafka publish failed: {details}")
    return published


def process_event_stream(
    events: Iterable[ConnectionEvent],
    *,
    runtime: AnalysisRuntime,
    provider: str = "template",
    store: Any | None = None,
    on_analysis: Callable[[ConnectionEvent, ConnectionAnalysis], None] | None = None,
) -> StreamSummary:
    """Analyze a stream and persist every generated alert ticket."""

    processed = alerts = stored = 0
    for event in events:
        analysis = analyze_raw_connection(
            event.record,
            runtime=runtime,
            source_ip=event.source_ip,
            provider=provider,
            explain_normal=False,
        )
        processed += 1
        if analysis.score.fused_anomaly:
            alerts += 1
            if store is not None:
                store.log_analysis(
                    analysis,
                    event_id=event.event_id,
                    source=event.source,
                    observed_at=event.observed_at,
                )
                stored += 1
        if on_analysis is not None:
            on_analysis(event, analysis)

    return StreamSummary(
        processed=processed,
        alerts=alerts,
        normals=processed - alerts,
        stored_tickets=stored,
    )


def _print_analysis(event: ConnectionEvent, analysis: ConnectionAnalysis) -> None:
    print(
        f"[{event.observed_at}] {event.event_id} {event.source_ip} -> "
        f"{analysis.predicted_class.upper()} "
        f"(fused confidence {analysis.score.fused_confidence:.1%}, "
        f"reason {analysis.score.alert_reason})"
    )
    if analysis.ticket:
        print(analysis.ticket)
        print()


def _add_dataset_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--train", type=Path, help="Path to KDDTrain+.txt.")
    parser.add_argument("--test", type=Path, help="Path to KDDTest+.txt.")


def _add_broker_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bootstrap-servers",
        default=os.getenv("KAFKA_BOOTSTRAP_SERVERS", DEFAULT_BOOTSTRAP_SERVERS),
        help="Comma-separated Kafka bootstrap servers.",
    )
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="Kafka topic name.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stream NSL-KDD-shaped connection events through the SOC assistant."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay", help="Analyze test rows with an event delay.")
    _add_dataset_arguments(replay)
    replay.add_argument("--start-index", type=int, default=0)
    replay.add_argument("--limit", type=_positive_int, default=10)
    replay.add_argument("--delay", type=_non_negative_float, default=0.5)

    consume = subparsers.add_parser("consume", help="Analyze events from Kafka.")
    _add_dataset_arguments(consume)
    _add_broker_arguments(consume)
    consume.add_argument("--group-id", default="ai-soc-assistant")
    consume.add_argument("--limit", type=_positive_int)
    consume.add_argument("--idle-timeout", type=_non_negative_float)

    publish = subparsers.add_parser("publish", help="Publish delayed test rows to Kafka.")
    _add_dataset_arguments(publish)
    _add_broker_arguments(publish)
    publish.add_argument("--start-index", type=int, default=0)
    publish.add_argument("--limit", type=_positive_int, default=10)
    publish.add_argument("--delay", type=_non_negative_float, default=0.5)

    for command_parser in (replay, consume):
        command_parser.add_argument(
            "--provider",
            default=os.getenv("SOC_LLM_PROVIDER", "template"),
            choices=["template", "ollama", "openai", "anthropic"],
        )
        command_parser.add_argument("--no-smote", action="store_true")
        command_parser.add_argument(
            "--isolation-threshold",
            type=_unit_interval,
            default=0.7,
        )
        command_parser.add_argument(
            "--database",
            type=Path,
            default=DEFAULT_DATABASE,
            help="SQLite database used for generated alert tickets.",
        )
        command_parser.add_argument(
            "--model",
            type=Path,
            help="Optional retrained model artifact. Without it, a baseline model is trained once.",
        )
        command_parser.add_argument(
            "--no-store",
            action="store_true",
            help="Do not persist generated alert tickets.",
        )
    return parser


def run(args: argparse.Namespace) -> StreamSummary | int:
    from src.ingest import load_nsl_kdd

    roots = [Path(__file__).resolve().parents[1], Path.cwd()]
    if args.command == "publish":
        dataset = load_nsl_kdd(args.train, args.test, search_roots=roots)
        events = replay_events(
            dataset.test,
            start_index=args.start_index,
            limit=args.limit,
            delay=args.delay,
        )
        return publish_events(
            events,
            bootstrap_servers=args.bootstrap_servers,
            topic=args.topic,
        )

    from src.feedback import FeedbackStore

    if args.model:
        from src.model_store import load_model_artifact, runtime_from_artifact

        dataset = load_nsl_kdd(args.train, args.test, search_roots=roots)
        runtime = runtime_from_artifact(
            load_model_artifact(args.model),
            dataset=dataset,
        )
    else:
        from src.runtime import build_runtime

        runtime = build_runtime(
            args.train,
            args.test,
            search_roots=roots,
            use_smote=not args.no_smote,
            isolation_threshold=args.isolation_threshold,
        )
    if args.command == "replay":
        events = replay_events(
            runtime.dataset.test,
            start_index=args.start_index,
            limit=args.limit,
            delay=args.delay,
        )
    else:
        events = kafka_events(
            bootstrap_servers=args.bootstrap_servers,
            topic=args.topic,
            group_id=args.group_id,
            limit=args.limit,
            idle_timeout=args.idle_timeout,
        )

    store = None if args.no_store else FeedbackStore(args.database)
    return process_event_stream(
        events,
        runtime=runtime,
        provider=args.provider,
        store=store,
        on_analysis=_print_analysis,
    )


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = run(args)
    except KeyboardInterrupt:
        print("\nStream stopped by analyst.")
        return
    if isinstance(result, int):
        print(f"Published {result} event(s) to {args.topic}.")
    else:
        print(
            f"Processed {result.processed} event(s): {result.alerts} alert(s), "
            f"{result.normals} normal, {result.stored_tickets} ticket(s) stored."
        )


if __name__ == "__main__":
    main()
