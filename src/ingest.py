"""Data ingestion utilities for the NSL-KDD SOC assistant pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

NSL_KDD_COLUMNS = [
    "duration",
    "protocol_type",
    "service",
    "flag",
    "src_bytes",
    "dst_bytes",
    "land",
    "wrong_fragment",
    "urgent",
    "hot",
    "num_failed_logins",
    "logged_in",
    "num_compromised",
    "root_shell",
    "su_attempted",
    "num_root",
    "num_file_creations",
    "num_shells",
    "num_access_files",
    "num_outbound_cmds",
    "is_host_login",
    "is_guest_login",
    "count",
    "srv_count",
    "serror_rate",
    "srv_serror_rate",
    "rerror_rate",
    "srv_rerror_rate",
    "same_srv_rate",
    "diff_srv_rate",
    "srv_diff_host_rate",
    "dst_host_count",
    "dst_host_srv_count",
    "dst_host_same_srv_rate",
    "dst_host_diff_srv_rate",
    "dst_host_same_src_port_rate",
    "dst_host_srv_diff_host_rate",
    "dst_host_serror_rate",
    "dst_host_srv_serror_rate",
    "dst_host_rerror_rate",
    "dst_host_srv_rerror_rate",
    "label",
    "difficulty",
]

CATEGORICAL_FEATURES = ["protocol_type", "service", "flag"]
TARGET_COLUMN = "attack_family"
DROP_COLUMNS = ["label", "difficulty", TARGET_COLUMN]

CLASS_NAMES = ["normal", "dos", "probe", "r2l", "u2r", "unknown"]
LABEL_MAP = {label: index for index, label in enumerate(CLASS_NAMES)}
INVERSE_LABEL_MAP = {index: label for label, index in LABEL_MAP.items()}

ATTACK_FAMILY_MAP = {
    "normal": "normal",
    # Denial of Service
    "apache2": "dos",
    "back": "dos",
    "land": "dos",
    "mailbomb": "dos",
    "neptune": "dos",
    "pod": "dos",
    "processtable": "dos",
    "smurf": "dos",
    "teardrop": "dos",
    "udpstorm": "dos",
    "worm": "dos",
    # Probe
    "ipsweep": "probe",
    "mscan": "probe",
    "nmap": "probe",
    "portsweep": "probe",
    "saint": "probe",
    "satan": "probe",
    # Remote to Local
    "ftp_write": "r2l",
    "guess_passwd": "r2l",
    "httptunnel": "r2l",
    "imap": "r2l",
    "multihop": "r2l",
    "named": "r2l",
    "phf": "r2l",
    "sendmail": "r2l",
    "snmpgetattack": "r2l",
    "snmpguess": "r2l",
    "spy": "r2l",
    "warezclient": "r2l",
    "warezmaster": "r2l",
    "xlock": "r2l",
    "xsnoop": "r2l",
    # User to Root
    "buffer_overflow": "u2r",
    "loadmodule": "u2r",
    "perl": "u2r",
    "ps": "u2r",
    "rootkit": "u2r",
    "sqlattack": "u2r",
    "xterm": "u2r",
}


@dataclass(frozen=True)
class DatasetPaths:
    """Resolved NSL-KDD train and test file paths."""

    train: Path
    test: Path | None


@dataclass(frozen=True)
class NslKddDataset:
    """Loaded train/test frames with attack-family labels attached."""

    train: pd.DataFrame
    test: pd.DataFrame
    paths: DatasetPaths


def _candidate_dataset_paths(filename: str, roots: Iterable[Path]) -> list[Path]:
    candidates: list[Path] = []
    for root in roots:
        candidates.extend([root / filename, root / "data" / filename])
    return candidates


def _first_existing_path(filename: str, roots: Iterable[Path]) -> Path | None:
    for candidate in _candidate_dataset_paths(filename, roots):
        if candidate.exists():
            return candidate
    return None


def resolve_dataset_paths(
    train_path: str | Path | None = None,
    test_path: str | Path | None = None,
    search_roots: Iterable[str | Path] | None = None,
    *,
    require_test: bool = True,
) -> DatasetPaths:
    """Resolve the required NSL-KDD files from explicit paths or known roots.

    ``KDDTest+.txt`` is optional for workflows that create a hold-out split from
    ``KDDTrain+.txt``. Cross-distribution evaluation and the interactive pipeline
    keep the safer default and require both files.
    """

    roots = [Path.cwd()]
    if search_roots is not None:
        roots.extend(Path(root) for root in search_roots)

    resolved_train = (
        Path(train_path) if train_path else _first_existing_path("KDDTrain+.txt", roots)
    )
    resolved_test = (
        Path(test_path)
        if test_path
        else _first_existing_path("KDDTest+.txt", roots) if require_test else None
    )

    missing = []
    if resolved_train is None or not resolved_train.exists():
        missing.append("KDDTrain+.txt")
    if require_test and (resolved_test is None or not resolved_test.exists()):
        missing.append("KDDTest+.txt")
    if missing:
        missing_files = ", ".join(missing)
        raise FileNotFoundError(
            f"Missing NSL-KDD dataset file(s): {missing_files}. "
            "Place them in the repo root, in data/, or pass --train and --test."
        )

    resolved_test_path = (
        resolved_test.resolve() if resolved_test is not None and resolved_test.exists() else None
    )
    return DatasetPaths(train=resolved_train.resolve(), test=resolved_test_path)


def add_attack_family(df: pd.DataFrame) -> pd.DataFrame:
    """Attach a normalized attack-family target column."""

    labeled = df.copy()
    labeled[TARGET_COLUMN] = labeled["label"].map(ATTACK_FAMILY_MAP).fillna("unknown")
    return labeled


def load_nsl_kdd(
    train_path: str | Path | None = None,
    test_path: str | Path | None = None,
    search_roots: Iterable[str | Path] | None = None,
    *,
    require_test: bool = True,
) -> NslKddDataset:
    """Load available NSL-KDD files and map fine-grained labels to families."""

    paths = resolve_dataset_paths(
        train_path,
        test_path,
        search_roots,
        require_test=require_test,
    )
    train_df = pd.read_csv(paths.train, names=NSL_KDD_COLUMNS)
    test_df = (
        pd.read_csv(paths.test, names=NSL_KDD_COLUMNS)
        if paths.test is not None
        else pd.DataFrame(columns=NSL_KDD_COLUMNS)
    )
    return NslKddDataset(
        train=add_attack_family(train_df),
        test=add_attack_family(test_df),
        paths=paths,
    )


def dataset_summary(dataset: NslKddDataset) -> str:
    """Return a compact, human-readable dataset summary."""

    train_counts = dataset.train[TARGET_COLUMN].value_counts().sort_index().to_dict()
    test_counts = dataset.test[TARGET_COLUMN].value_counts().sort_index().to_dict()
    return (
        f"Loaded {len(dataset.train):,} training rows and {len(dataset.test):,} test rows.\n"
        f"Training families: {train_counts}\n"
        f"Test families: {test_counts}"
    )
