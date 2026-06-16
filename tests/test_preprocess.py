"""Tests for the one-hot encoding / scaling preprocessing pipeline."""

from pathlib import Path

import pandas as pd

from src.ingest import DatasetPaths, NslKddDataset, add_attack_family
from src.preprocess import preprocess_dataset


def _tiny_dataset(train_services, test_services):
    """Build a minimal NSL-KDD-shaped dataset for fast unit tests."""

    def frame(services, labels):
        return add_attack_family(
            pd.DataFrame(
                {
                    "protocol_type": ["tcp"] * len(services),
                    "service": services,
                    "flag": ["SF"] * len(services),
                    "src_bytes": [100, 200, 300, 400][: len(services)],
                    "count": [1, 2, 3, 4][: len(services)],
                    "label": labels,
                    "difficulty": [20] * len(services),
                }
            )
        )

    train = frame(train_services, ["normal", "neptune", "nmap", "guess_passwd"])
    test = frame(test_services, ["normal", "neptune"])
    paths = DatasetPaths(train=Path("train.txt"), test=Path("test.txt"))
    return NslKddDataset(train=train, test=test, paths=paths)


def test_one_hot_encoder_ignores_unknown_test_categories():
    # "telnet" never appears in training, so a naive LabelEncoder would crash.
    dataset = _tiny_dataset(
        train_services=["http", "ftp", "http", "ftp"],
        test_services=["http", "telnet"],
    )

    data = preprocess_dataset(dataset, use_smote=False)

    # Train and test share an identical feature space...
    assert list(data.x_train.columns) == list(data.x_test.columns)
    # ...and the unseen "telnet" service did not create a new column.
    assert "service_telnet" not in data.x_test.columns
    # The unknown category is encoded as all-zeros rather than raising.
    assert data.x_test.isna().to_numpy().sum() == 0


def test_feature_names_exclude_targets_and_raw_categoricals():
    dataset = _tiny_dataset(
        train_services=["http", "ftp", "http", "ftp"],
        test_services=["http", "ftp"],
    )

    data = preprocess_dataset(dataset, use_smote=False)

    for dropped in ("label", "difficulty", "attack_family", "service", "protocol_type", "flag"):
        assert dropped not in data.feature_names
    assert data.x_train_scaled.shape[1] == len(data.feature_names)


def test_targets_are_integer_class_indices():
    dataset = _tiny_dataset(
        train_services=["http", "ftp", "http", "ftp"],
        test_services=["http", "ftp"],
    )

    data = preprocess_dataset(dataset, use_smote=False)

    # normal=0, dos=1, probe=2, r2l=3 per LABEL_MAP.
    assert data.y_train.tolist() == [0, 1, 2, 3]
    assert data.y_train.dtype.kind == "i"
