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

    import pandas as pd

    from src.agent import generate_incident_ticket
    from src.explain import explain_connection
    from src.ingest import INVERSE_LABEL_MAP
    from src.train import score_connection

    row_df = pd.DataFrame([raw_row], columns=data.feature_names)
    row_scaled = data.scaler.transform(row_df)[0]

    score = score_connection(models, row_scaled)

    if not score.fused_anomaly:
        return "Connection classified NORMAL - no ticket generated."

    shap_bundle = explain_connection(
        explainer=explainer,
        random_forest=models.random_forest,
        raw_row_df=row_df,
        row_scaled=row_scaled,
        prediction=score.rf_prediction,
        feature_names=data.feature_names,
    )
    rf_predicted_class = INVERSE_LABEL_MAP.get(
        score.rf_prediction,
        f"class_{score.rf_prediction}",
    )
    shap_bundle["rf_predicted_class"] = rf_predicted_class
    if score.alert_reason == "isolation_forest":
        shap_bundle["predicted_class"] = "anomaly"
    shap_bundle["rf_confidence"] = score.rf_confidence
    shap_bundle["rf_anomaly_confidence"] = score.rf_anomaly_confidence
    shap_bundle["isolation_forest_score"] = score.isolation_score
    shap_bundle["isolation_risk"] = score.isolation_risk
    shap_bundle["isolation_threshold"] = models.isolation_threshold
    shap_bundle["rf_anomaly"] = score.rf_anomaly
    shap_bundle["isolation_anomaly"] = score.isolation_anomaly
    shap_bundle["fused_anomaly"] = score.fused_anomaly
    shap_bundle["fused_confidence"] = score.fused_confidence
    shap_bundle["alert_reason"] = score.alert_reason
    shap_bundle["source_ip"] = src_ip or "unknown"

    return generate_incident_ticket(
        shap_bundle,
        src_ip=src_ip,
        provider=provider,
    )


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
    return parser


def run_pipeline(args: argparse.Namespace) -> str:
    from src.explain import build_explainer
    from src.ingest import dataset_summary, load_nsl_kdd
    from src.preprocess import preprocess_dataset
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
    ticket = process_connection(
        data.x_test.iloc[selected_index],
        data=data,
        models=models,
        explainer=explainer,
        src_ip=args.src_ip,
        provider=provider,
    )
    return ticket


def main() -> None:
    args = build_parser().parse_args()
    ticket = run_pipeline(args)
    print("\n=== SOC Incident Ticket ===\n")
    print(ticket)


if __name__ == "__main__":
    main()
