"""Preprocessing pipeline for NSL-KDD connection records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from imblearn.over_sampling import RandomOverSampler
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.ingest import (
    CATEGORICAL_FEATURES,
    DROP_COLUMNS,
    LABEL_MAP,
    MODEL_INPUT_COLUMNS,
    TARGET_COLUMN,
    NslKddDataset,
)


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
    balancing_applied: bool
    balancing_method: str

    @property
    def smote_applied(self) -> bool:
        """Backward-compatible alias for the former balancing status field."""

        return self.balancing_applied


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


def _apply_random_oversampling(
    x_train_scaled: np.ndarray,
    y_train: pd.Series,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Balance classes by duplicating exact rows without synthesizing telemetry."""

    class_counts = y_train.value_counts()
    if len(class_counts) < 2:
        return x_train_scaled, y_train.to_numpy(), False

    sampler = RandomOverSampler(random_state=random_state)
    x_balanced, y_balanced = sampler.fit_resample(x_train_scaled, y_train)
    balancing_applied = len(x_balanced) > len(x_train_scaled)
    return np.asarray(x_balanced), np.asarray(y_balanced, dtype=int), balancing_applied


def transform_connections(
    raw_rows: pd.DataFrame,
    preprocessor: Any,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Transform raw NSL-KDD-shaped records with a fitted preprocessor.

    Extra metadata fields are ignored. All 41 model input fields are required so
    replayed records, Kafka events, and dashboard submissions follow the exact
    same feature contract as training data.
    """

    if raw_rows.empty:
        raise ValueError("At least one connection record is required.")

    missing = [column for column in MODEL_INPUT_COLUMNS if column not in raw_rows.columns]
    if missing:
        raise ValueError(f"Connection record is missing required fields: {', '.join(missing)}")

    frame = raw_rows.loc[:, MODEL_INPUT_COLUMNS].copy().reset_index(drop=True)
    contains_nested_values = frame.map(
        lambda value: isinstance(value, (Mapping, list, tuple, set))
    ).to_numpy()
    if contains_nested_values.any():
        raise ValueError("Connection fields must contain scalar values.")
    numeric_columns = [
        column for column in MODEL_INPUT_COLUMNS if column not in CATEGORICAL_FEATURES
    ]
    try:
        frame[numeric_columns] = frame[numeric_columns].apply(
            pd.to_numeric,
            errors="raise",
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Numeric connection fields must contain valid numbers.") from exc
    if not np.isfinite(frame[numeric_columns].to_numpy(dtype=float)).all():
        raise ValueError("Numeric connection fields must all be finite.")

    frame[CATEGORICAL_FEATURES] = frame[CATEGORICAL_FEATURES].astype(str)
    encoded = preprocessor.encoder.transform(frame[CATEGORICAL_FEATURES])
    encoded_columns = list(preprocessor.encoder.get_feature_names_out(CATEGORICAL_FEATURES))
    encoded_frame = pd.DataFrame(encoded, columns=encoded_columns, index=frame.index)
    processed = pd.concat(
        [frame.drop(columns=CATEGORICAL_FEATURES), encoded_frame],
        axis=1,
    )

    expected_features = list(preprocessor.feature_names)
    unexpected = [column for column in processed.columns if column not in expected_features]
    if unexpected:
        processed = processed.drop(columns=unexpected)
    processed = processed.reindex(columns=expected_features, fill_value=0.0)
    scaled = np.asarray(preprocessor.scaler.transform(processed), dtype=float)
    return processed, scaled


def transform_connection(
    raw_record: Mapping[str, Any] | pd.Series,
    preprocessor: Any,
) -> tuple[pd.Series, np.ndarray]:
    """Transform one raw connection and return its processed and scaled forms."""

    record = raw_record.to_dict() if isinstance(raw_record, pd.Series) else dict(raw_record)
    processed, scaled = transform_connections(pd.DataFrame([record]), preprocessor)
    return processed.iloc[0], scaled[0]


def preprocess_dataset(
    dataset: NslKddDataset,
    *,
    use_smote: bool = True,
    random_state: int = 42,
    smote_k_neighbors: int = 3,
) -> PreprocessedData:
    """One-hot encode, scale, and optionally balance the NSL-KDD train/test split.

    ``use_smote`` and ``smote_k_neighbors`` retain their historical names for API
    compatibility (the neighbor setting is no longer used). Balancing now uses
    exact-row random oversampling so categorical, binary, and integer-domain
    NSL-KDD features are never interpolated.
    """

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
        x_train_balanced, y_train_balanced, balancing_applied = _apply_random_oversampling(
            x_train_scaled,
            y_train,
            random_state=random_state,
        )
    else:
        x_train_balanced = x_train_scaled
        y_train_balanced = y_train.to_numpy()
        balancing_applied = False

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
        balancing_applied=balancing_applied,
        balancing_method="random_oversampling" if balancing_applied else "none",
    )
