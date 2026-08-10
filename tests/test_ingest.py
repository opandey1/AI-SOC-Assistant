"""Tests for NSL-KDD ingestion and attack-family mapping."""

import pandas as pd
import pytest

from src.ingest import (
    CLASS_NAMES,
    INVERSE_LABEL_MAP,
    LABEL_MAP,
    add_attack_family,
    resolve_dataset_paths,
)


def test_add_attack_family_maps_known_and_unknown_labels():
    df = pd.DataFrame(
        {
            "label": [
                "normal",
                "neptune",  # dos
                "nmap",  # probe
                "guess_passwd",  # r2l
                "buffer_overflow",  # u2r
                "brand_new_zero_day",  # unmapped -> unknown
            ]
        }
    )

    families = add_attack_family(df)["attack_family"].tolist()

    assert families == ["normal", "dos", "probe", "r2l", "u2r", "unknown"]


def test_label_map_round_trips_for_every_class():
    assert list(LABEL_MAP) == CLASS_NAMES
    for name in CLASS_NAMES:
        assert INVERSE_LABEL_MAP[LABEL_MAP[name]] == name


def test_add_attack_family_does_not_mutate_input():
    df = pd.DataFrame({"label": ["neptune"]})
    add_attack_family(df)
    assert "attack_family" not in df.columns


def test_holdout_workflow_can_resolve_training_file_without_test_file(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    train_path = tmp_path / "KDDTrain+.txt"
    train_path.write_text("placeholder", encoding="utf-8")

    paths = resolve_dataset_paths(train_path=train_path, require_test=False)

    assert paths.train == train_path.resolve()
    assert paths.test is None


def test_default_dataset_resolution_still_requires_test_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    train_path = tmp_path / "KDDTrain+.txt"
    train_path.write_text("placeholder", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match=r"KDDTest\+\.txt"):
        resolve_dataset_paths(train_path=train_path)
