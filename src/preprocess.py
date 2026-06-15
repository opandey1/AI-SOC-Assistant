"""Preprocessing pipeline for NSL-KDD connection records."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from imblearn.over_sampling import SMOTE
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.ingest import CATEGORICAL_FEATURES, DROP_COLUMNS, LABEL_MAP, TARGET_COLUMN, NslKddDataset


@dataclass(frozen=True)
class PreprocessedData:
    """Feature matrices, targets, and fitted preprocessing objects."""

    raw_train: pd.DataFrame
    raw_test: pd.DataFrame
    train: pd.DataFrame
    test: pd.DataFrame
    x_train: pd.DataFrame
    x_test: pd.DataFrame
    y_train: pd.Series
    y_test: pd.Series
    x_train_scaled: np.ndarray
    x_test_scaled: np.ndarray
    x_train_balanced: np.ndarray
    y_train_balanced: np.ndarray
    feature_names: list[str]
    encoder: OneHotEncoder
    scaler: StandardScaler
    smote_applied: bool


def _make_one_hot_encoder() -> OneHotEncoder:
    """Create an encoder that supports both newer and older sklearn versions."""

    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def _encode_categorical_columns(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    encoder: OneHotEncoder,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    train_encoded = encoder.fit_transform(train_df[CATEGORICAL_FEATURES])
    test_encoded = encoder.transform(test_df[CATEGORICAL_FEATURES])
    encoded_columns = list(encoder.get_feature_names_out(CATEGORICAL_FEATURES))

    train_ohe = pd.DataFrame(train_encoded, columns=encoded_columns, index=train_df.index)
    test_ohe = pd.DataFrame(test_encoded, columns=encoded_columns, index=test_df.index)

    train_processed = pd.concat(
        [train_df.drop(columns=CATEGORICAL_FEATURES), train_ohe],
        axis=1,
    )
    test_processed = pd.concat(
        [test_df.drop(columns=CATEGORICAL_FEATURES), test_ohe],
        axis=1,
    )
    return train_processed, test_processed, encoded_columns


def _build_targets(train_df: pd.DataFrame, test_df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    y_train = train_df[TARGET_COLUMN].map(LABEL_MAP)
    y_test = test_df[TARGET_COLUMN].map(LABEL_MAP)
    if y_train.isna().any() or y_test.isna().any():
        raise ValueError("One or more attack families could not be mapped to numeric labels.")
    return y_train.astype(int), y_test.astype(int)


def _apply_smote(
    x_train_scaled: np.ndarray,
    y_train: pd.Series,
    random_state: int,
    default_k_neighbors: int,
) -> tuple[np.ndarray, np.ndarray, bool]:
    class_counts = y_train.value_counts()
    min_class_count = int(class_counts.min())
    if min_class_count <= 1:
        return x_train_scaled, y_train.to_numpy(), False

    k_neighbors = min(default_k_neighbors, min_class_count - 1)
    smote = SMOTE(random_state=random_state, k_neighbors=k_neighbors)
    x_balanced, y_balanced = smote.fit_resample(x_train_scaled, y_train)
    return x_balanced, y_balanced, True


def preprocess_dataset(
    dataset: NslKddDataset,
    *,
    use_smote: bool = True,
    random_state: int = 42,
    smote_k_neighbors: int = 3,
) -> PreprocessedData:
    """One-hot encode, scale, and optionally balance the NSL-KDD train/test split."""

    raw_train = dataset.train.copy()
    raw_test = dataset.test.copy()

    encoder = _make_one_hot_encoder()
    train_processed, test_processed, _ = _encode_categorical_columns(raw_train, raw_test, encoder)

    y_train, y_test = _build_targets(train_processed, test_processed)
    x_train = train_processed.drop(columns=DROP_COLUMNS)
    x_test = test_processed.drop(columns=DROP_COLUMNS)

    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_test_scaled = scaler.transform(x_test)

    if use_smote:
        x_train_balanced, y_train_balanced, smote_applied = _apply_smote(
            x_train_scaled,
            y_train,
            random_state=random_state,
            default_k_neighbors=smote_k_neighbors,
        )
    else:
        x_train_balanced = x_train_scaled
        y_train_balanced = y_train.to_numpy()
        smote_applied = False

    return PreprocessedData(
        raw_train=raw_train,
        raw_test=raw_test,
        train=train_processed,
        test=test_processed,
        x_train=x_train,
        x_test=x_test,
        y_train=y_train,
        y_test=y_test,
        x_train_scaled=x_train_scaled,
        x_test_scaled=x_test_scaled,
        x_train_balanced=x_train_balanced,
        y_train_balanced=y_train_balanced,
        feature_names=list(x_train.columns),
        encoder=encoder,
        scaler=scaler,
        smote_applied=smote_applied,
    )
