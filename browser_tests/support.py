"""Isolated, unmodified application copies for browser regression tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

from src.ingest import MODEL_INPUT_COLUMNS, NSL_KDD_COLUMNS

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AppWorkspace:
    root: Path
    owner: str
    records: dict[str, dict]

    @property
    def database(self) -> Path:
        return self.root / "state" / "soc_feedback.db"


def synthetic_records() -> dict[str, dict]:
    """Clearly separated placeholders, not captured traffic or model-quality evidence."""
    normal = dict.fromkeys(MODEL_INPUT_COLUMNS, 0)
    normal.update(protocol_type="tcp", service="http", flag="SF", src_bytes=1000, dst_bytes=2000)
    records = {"normal": normal}
    for family, changes in {
        "dos": dict(service="private", flag="S0", duration=100, count=200, serror_rate=1),
        "probe": dict(service="ftp", flag="REJ", duration=10, count=50, diff_srv_rate=1),
        "r2l": dict(service="ftp_data", duration=20, num_failed_logins=5, logged_in=1),
        "u2r": dict(duration=30, root_shell=1, num_shells=5, hot=10),
    }.items():
        record = dict(normal)
        record.update(src_bytes=0, dst_bytes=0, **changes)
        records[family] = record
    return records


def _dataset_row(record: dict, label: str) -> str:
    values = {**record, "label": label, "difficulty": 20}
    return ",".join(str(values[column]) for column in NSL_KDD_COLUMNS)


def prepare_workspace(destination: Path, source: Path = ROOT) -> AppWorkspace:
    """Copy only application sources/config/fonts; never copy state, models or secrets."""
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source / "streamlit_app.py", destination / "streamlit_app.py")
    (destination / "src").mkdir()
    for module in (source / "src").glob("*.py"):
        shutil.copy2(module, destination / "src" / module.name)
    (destination / ".streamlit").mkdir()
    shutil.copy2(source / ".streamlit/config.toml", destination / ".streamlit/config.toml")
    shutil.copytree(source / "static/fonts", destination / "static/fonts")
    (destination / "home").mkdir()
    owner = uuid4().hex
    (destination / "static/browser-owner.txt").write_text(owner, encoding="utf-8")
    records = synthetic_records()
    labels = dict(
        normal="normal", dos="neptune", probe="satan", r2l="guess_passwd", u2r="buffer_overflow"
    )
    (destination / "data").mkdir()
    train = [
        _dataset_row(records[family], label) for family, label in labels.items() for _ in range(16)
    ]
    test = [_dataset_row(records[family], labels[family]) for family in ("dos", "dos", "normal")]
    for filename, rows in (("KDDTrain+.txt", train), ("KDDTest+.txt", test)):
        (destination / "data" / filename).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return AppWorkspace(destination, owner, records)


def child_environment(workspace: AppWorkspace) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("SOC_", "STREAMLIT_"))
        and key.upper() not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ABUSEIPDB_API_KEY"}
    }
    env.update(
        PYTHONPATH=str(workspace.root),
        PYTHONUNBUFFERED="1",
        SOC_LLM_PROVIDER="template",
        SOC_ENABLE_THREAT_INTEL="false",
        HOME=str(workspace.root / "home"),
        USERPROFILE=str(workspace.root / "home"),
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
    )
    return env


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_review_database(page, database: Path, *, timeout: float = 30_000) -> None:
    """Wait for the native widget's default, not just its initially empty DOM input."""

    from playwright.sync_api import expect

    expect(page.get_by_role("textbox", name="Review database", exact=True)).to_have_value(
        str(database), timeout=timeout
    )


def wait_for_server(process: subprocess.Popen, url: str, owner: str, timeout: float = 45) -> None:
    """Require the unique workspace marker, not just another app's health endpoint."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Test Streamlit process exited with code {process.returncode}.")
        try:
            with urlopen(f"{url}/app/static/browser-owner.txt", timeout=1) as response:
                if response.read().decode("utf-8") == owner:
                    return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.1)
    raise TimeoutError("The isolated Streamlit server did not become ready. Inspect server.log.")


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # venv launchers can have a child interpreter; terminate only this owned tree.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def start_server(workspace: AppWorkspace, log_path: Path) -> tuple[subprocess.Popen, str]:
    port = unused_port()
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "streamlit_app.py",
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--server.fileWatcherType",
        "none",
        "--browser.gatherUsageStats",
        "false",
    ]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=workspace.root,
            env=child_environment(workspace),
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    url = f"http://127.0.0.1:{port}"
    try:
        wait_for_server(process, url, workspace.owner)
    except BaseException:
        stop_server(process)
        raise
    return process, url


def write_manifest(workspace: AppWorkspace, destination: Path) -> None:
    paths = [workspace.root / "streamlit_app.py", *sorted((workspace.root / "src").glob("*.py"))]
    paths.extend(sorted((workspace.root / "data").glob("*.txt")))
    manifest = {
        "fixture": "synthetic placeholders; 80 training rows, 3 replay rows; not a quality benchmark",
        "provider": "template",
        "python": sys.version,
        "sha256": {
            str(path.relative_to(workspace.root))
            .replace("\\", "/"): hashlib.sha256(path.read_bytes())
            .hexdigest()
            for path in paths
        },
    }
    destination.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
