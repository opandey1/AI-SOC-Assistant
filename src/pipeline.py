"""Command-line pipeline for the AI-powered SOC assistant."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _unit_interval(value: str) -> float:
    """Parse a finite inclusive 0-1 value for argparse."""

    import math

    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("value must be between 0 and 1")
    return parsed


def select_connection_index(models: Any, row_index: int | None = None) -> int:
    """Select a requested row, or the first fused anomaly in the test set."""

    import numpy as np

    if row_index is not None:
        if row_index < 0 or row_index >= len(models.fused_anomaly):
            raise IndexError(f"row-index {row_index} is outside the test-set range.")
        return row_index

    flagged_indices = np.flatnonzero(models.fused_anomaly == 1)
    if len(flagged_indices) == 0:
        return 0
    return int(flagged_indices[0])


def process_connection(
    raw_row: "pd.Series",
    *,
    data: Any,
    models: Any,
    explainer: Any,
    src_ip: str | None = None,
    provider: str | None = None,
) -> str:
    """Classify one processed connection row and return an SOC ticket or normal verdict."""

    from src.runtime import AnalysisRuntime, NORMAL_VERDICT, analyze_processed_connection

    runtime = AnalysisRuntime(
        dataset=None,
        preprocessor=data,
        models=models,
        explainer=explainer,
    )
    analysis = analyze_processed_connection(
        raw_row,
        runtime=runtime,
        source_ip=src_ip,
        provider=provider,
        explain_normal=False,
    )
    return analysis.ticket or NORMAL_VERDICT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train the AI-SOC-Assistant pipeline and generate an analyst-ready incident ticket.",
    )
    parser.add_argument("--train", type=Path, help="Path to KDDTrain+.txt.")
    parser.add_argument("--test", type=Path, help="Path to KDDTest+.txt.")
    parser.add_argument(
        "--row-index", type=int, help="Specific test-set row to process. Defaults to first anomaly."
    )
    parser.add_argument(
        "--src-ip", default="192.168.1.47", help="Source IP to include in the generated ticket."
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("SOC_LLM_PROVIDER", "ollama"),
        choices=["ollama", "openai", "anthropic", "template"],
        help="LLM provider for ticket generation.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Use the deterministic template ticket instead of invoking an LLM.",
    )
    parser.add_argument(
        "--no-smote",
        action="store_true",
        help="Disable class balancing (legacy flag name).",
    )
    parser.add_argument(
        "--isolation-threshold",
        type=_unit_interval,
        default=0.7,
        help="Normalized Isolation Forest threshold used in fused anomaly scoring.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("state") / "soc_feedback.db",
        help="SQLite database used to persist generated alert tickets.",
    )
    parser.add_argument(
        "--no-store",
        action="store_true",
        help="Do not persist a generated alert ticket.",
    )
    return parser


def run_pipeline(args: argparse.Namespace) -> str:
    from datetime import datetime, timezone
    from uuid import uuid4

    from src.explain import build_explainer
    from src.ingest import dataset_summary, load_nsl_kdd
    from src.preprocess import preprocess_dataset
    from src.runtime import AnalysisRuntime, NORMAL_VERDICT, analyze_processed_connection
    from src.train import train_models

    project_root = Path(__file__).resolve().parents[1]
    dataset = load_nsl_kdd(args.train, args.test, search_roots=[project_root, Path.cwd()])
    print(dataset_summary(dataset))

    data = preprocess_dataset(dataset, use_smote=not args.no_smote)
    print(
        f"Prepared {len(data.feature_names):,} model features. "
        f"Class balancing: {data.balancing_method}."
    )

    models = train_models(data, isolation_threshold=args.isolation_threshold)
    selected_index = select_connection_index(models, args.row_index)
    print(f"Selected test row: {selected_index}")

    explainer = build_explainer(models.random_forest)
    provider = "template" if args.no_llm else args.provider
    runtime = AnalysisRuntime(
        dataset=dataset,
        preprocessor=data,
        models=models,
        explainer=explainer,
    )
    analysis = analyze_processed_connection(
        data.x_test.iloc[selected_index],
        runtime=runtime,
        raw_record=data.raw_test.iloc[selected_index],
        source_ip=args.src_ip,
        provider=provider,
        explain_normal=False,
    )
    if analysis.ticket and not args.no_store:
        from src.feedback import FeedbackStore

        FeedbackStore(args.database).log_analysis(
            analysis,
            event_id=f"cli-{uuid4()}",
            source="pipeline-cli",
            observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    return analysis.ticket or NORMAL_VERDICT


def main() -> None:
    args = build_parser().parse_args()
    ticket = run_pipeline(args)
    print("\n=== SOC Incident Ticket ===\n")
    print(ticket)


if __name__ == "__main__":
    main()
