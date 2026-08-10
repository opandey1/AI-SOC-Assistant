"""Tests for evaluation protocol metadata and artifact isolation."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.evaluate import (
    _build_holdout_dataset,
    _confusion_matrix_title,
    _holdout_protocol,
    build_parser,
    evaluate_predictions,
    evaluation_output_dir,
    run_evaluation,
)
from src.ingest import DatasetPaths, NSL_KDD_COLUMNS, NslKddDataset, add_attack_family


def _write_tiny_nsl_train(path):
    rows = []
    for index, label in enumerate(["normal", "neptune"] * 4):
        row = {column: 0 for column in NSL_KDD_COLUMNS}
        row.update(
            {
                "protocol_type": "tcp",
                "service": "http" if index % 2 == 0 else "ftp",
                "flag": "SF" if index % 2 == 0 else "S0",
                "src_bytes": 100 + index,
                "count": index + 1,
                "label": label,
                "difficulty": 20,
            }
        )
        rows.append(row)
    pd.DataFrame(rows, columns=NSL_KDD_COLUMNS).to_csv(path, header=False, index=False)


def test_holdout_protocol_reflects_requested_validation_fraction():
    assert _holdout_protocol(0.25, stratified=True) == (
        "stratified 75/25 hold-out split of KDDTrain+"
    )
    assert _holdout_protocol(0.125, stratified=False) == ("87.5/12.5 hold-out split of KDDTrain+")


def test_evaluation_output_directories_are_protocol_specific(tmp_path):
    holdout = evaluation_output_dir(tmp_path, use_test_set=False)
    cross_distribution = evaluation_output_dir(tmp_path, use_test_set=True)

    assert holdout == tmp_path / "holdout"
    assert cross_distribution == tmp_path / "cross_distribution"
    assert holdout != cross_distribution


def test_confusion_matrix_title_uses_actual_class_count_and_protocol():
    report = evaluate_predictions(
        np.array([0, 0]),
        np.array([0, 0]),
        protocol="single-class smoke test",
    )

    title = _confusion_matrix_title(report)

    assert "1-class" in title
    assert "single-class smoke test" in title


def test_single_class_holdout_split_is_handled_gracefully():
    train = add_attack_family(
        pd.DataFrame(
            {
                "label": ["normal"] * 4,
                "protocol_type": ["tcp"] * 4,
                "service": ["http"] * 4,
                "flag": ["SF"] * 4,
            }
        )
    )
    dataset = NslKddDataset(
        train=train,
        test=train.iloc[0:0].copy(),
        paths=DatasetPaths(train=Path("train.txt"), test=None),
    )

    split = _build_holdout_dataset(dataset, random_state=0, val_size=0.25)

    assert len(split.train) == 3
    assert len(split.test) == 1
    assert split.train["attack_family"].unique().tolist() == ["normal"]


@pytest.mark.parametrize("value", ["0", "1", "-0.1", "1.1", "nan", "inf"])
def test_cli_rejects_invalid_holdout_fraction(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--val-size", value])


def test_holdout_evaluation_does_not_require_kdd_test_file(tmp_path):
    train_path = tmp_path / "KDDTrain+.txt"
    _write_tiny_nsl_train(train_path)
    output_base = tmp_path / "artifacts"
    args = argparse.Namespace(
        train=train_path,
        test=tmp_path / "missing-KDDTest+.txt",
        output_dir=output_base,
        use_test_set=False,
        val_size=0.25,
        random_state=0,
        no_smote=True,
        skip_shap=True,
    )

    report = run_evaluation(args)

    assert "75/25" in report.protocol
    assert (output_base / "holdout" / "metrics.json").is_file()
    assert (output_base / "holdout" / "confusion_matrix.png").is_file()
