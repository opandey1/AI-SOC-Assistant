"""Tests for NSL-KDD ingestion and attack-family mapping."""

import pandas as pd

from src.ingest import (
    CLASS_NAMES,
    INVERSE_LABEL_MAP,
    LABEL_MAP,
    add_attack_family,
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
