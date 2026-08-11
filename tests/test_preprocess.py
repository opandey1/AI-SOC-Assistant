"""Tests for the one-hot encoding / scaling preprocessing pipeline."""

from pathlib import Path

import numpy as np
import pandas as pd

from src.ingest import (
    CATEGORICAL_FEATURES,
    MODEL_INPUT_COLUMNS,
    DatasetPaths,
    NslKddDataset,
    add_attack_family,
)
from src.preprocess import preprocess_dataset, transform_connection


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


def test_random_oversampling_preserves_exact_rows_and_feature_domains():
    labels = ["normal"] * 6 + ["neptune"] * 3
    train = add_attack_family(
        pd.DataFrame(
            {
                "protocol_type": ["tcp", "udp", "tcp"] * 3,
                "service": ["http", "domain_u", "ftp"] * 3,
                "flag": ["SF", "S0", "REJ"] * 3,
                "src_bytes": [
                    10,
                    1_000_000_000_000,
                    30,
                    40,
                    2_000_000_000_000,
                    60,
                    70,
                    3_000_000_000_000,
                    90,
                ],
                "land": [0, 1, 0] * 3,
                "logged_in": [1, 0, 1] * 3,
                "num_failed_logins": [0] * 9,
                "count": list(range(1, 10)),
                "label": labels,
                "difficulty": [20] * 9,
            }
        )
    )
    test = train.iloc[:2].copy()
    dataset = NslKddDataset(
        train=train,
        test=test,
        paths=DatasetPaths(train=Path("train.txt"), test=Path("test.txt")),
    )

    data = preprocess_dataset(dataset, use_smote=True, smote_k_neighbors=2)

    assert data.smote_applied is True
    assert data.balancing_method == "random_oversampling"
    assert len(data.x_train_balanced) > len(data.x_train_scaled)
    balanced_counts = pd.Series(data.y_train_balanced).value_counts()
    assert balanced_counts.nunique() == 1
    balanced_unscaled = pd.DataFrame(
        data.scaler.inverse_transform(data.x_train_balanced),
        columns=data.feature_names,
    )

    for balanced_row in data.x_train_balanced:
        exact_match = np.all(data.x_train_scaled == balanced_row, axis=1)
        assert exact_match.any()

    for feature in ("protocol_type", "service", "flag"):
        category_columns = [
            column for column in data.feature_names if column.startswith(f"{feature}_")
        ]
        category_values = balanced_unscaled[category_columns].to_numpy()
        assert np.allclose(category_values.sum(axis=1), 1.0)
        assert np.all(np.isclose(category_values, 0.0) | np.isclose(category_values, 1.0))
    for binary_feature in ("land", "logged_in", "num_failed_logins"):
        assert set(np.rint(balanced_unscaled[binary_feature]).astype(int)) <= {0, 1}
    assert np.allclose(balanced_unscaled["count"], np.rint(balanced_unscaled["count"]))


def test_smote_is_skipped_gracefully_for_single_class_training_data():
    train = add_attack_family(
        pd.DataFrame(
            {
                "protocol_type": ["tcp"] * 4,
                "service": ["http"] * 4,
                "flag": ["SF"] * 4,
                "src_bytes": [10, 20, 30, 40],
                "count": [1, 2, 3, 4],
                "label": ["normal"] * 4,
                "difficulty": [20] * 4,
            }
        )
    )
    dataset = NslKddDataset(
        train=train,
        test=train.iloc[:2].copy(),
        paths=DatasetPaths(train=Path("train.txt"), test=Path("test.txt")),
    )

    data = preprocess_dataset(dataset, use_smote=True)

    assert data.smote_applied is False
    assert data.balancing_method == "none"
    np.testing.assert_array_equal(data.x_train_balanced, data.x_train_scaled)
    assert data.y_train_balanced.tolist() == [0, 0, 0, 0]


def test_random_oversampling_can_balance_a_single_minority_sample():
    train = add_attack_family(
        pd.DataFrame(
            {
                "protocol_type": ["tcp"] * 4,
                "service": ["http"] * 4,
                "flag": ["SF"] * 4,
                "src_bytes": [10, 20, 30, 40],
                "count": [1, 2, 3, 4],
                "label": ["normal", "normal", "normal", "neptune"],
                "difficulty": [20] * 4,
            }
        )
    )
    dataset = NslKddDataset(
        train=train,
        test=train.iloc[:2].copy(),
        paths=DatasetPaths(train=Path("train.txt"), test=Path("test.txt")),
    )

    data = preprocess_dataset(dataset, use_smote=True)

    assert pd.Series(data.y_train_balanced).value_counts().to_dict() == {0: 3, 1: 3}
    assert data.balancing_method == "random_oversampling"


def _complete_raw_row(seed=0):
    row = {column: seed for column in MODEL_INPUT_COLUMNS}
    row.update({"protocol_type": "tcp", "service": "http", "flag": "SF"})
    return row


def test_transform_connection_matches_batch_preprocessing_and_rejects_bad_input():
    train_rows = []
    for seed, label in enumerate(["normal", "neptune", "nmap", "guess_passwd"]):
        row = _complete_raw_row(seed)
        row.update({"label": label, "difficulty": 20})
        train_rows.append(row)
    test_row = _complete_raw_row(7)
    test_row.update({"service": "unseen-service", "label": "normal", "difficulty": 20})
    dataset = NslKddDataset(
        train=add_attack_family(pd.DataFrame(train_rows)),
        test=add_attack_family(pd.DataFrame([test_row])),
        paths=DatasetPaths(train=Path("train.txt"), test=Path("test.txt")),
    )
    data = preprocess_dataset(dataset, use_smote=False)

    transformed, scaled = transform_connection(test_row, data)

    pd.testing.assert_series_equal(transformed, data.x_test.iloc[0], check_names=False)
    np.testing.assert_allclose(scaled, data.x_test_scaled[0])

    missing = test_row.copy()
    missing.pop("duration")
    with np.testing.assert_raises_regex(ValueError, "missing required fields"):
        transform_connection(missing, data)

    invalid = test_row.copy()
    invalid["duration"] = np.inf
    with np.testing.assert_raises_regex(ValueError, "must all be finite"):
        transform_connection(invalid, data)

    nested = test_row.copy()
    nested["service"] = ["http"]
    with np.testing.assert_raises_regex(ValueError, "scalar values"):
        transform_connection(nested, data)

    assert all(column in test_row for column in CATEGORICAL_FEATURES)
