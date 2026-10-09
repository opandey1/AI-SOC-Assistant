"""Safety checks for the browser harness without requiring Playwright or browsers."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pandas as pd
import pytest

from browser_tests import support
from src.ingest import MODEL_INPUT_COLUMNS, NSL_KDD_COLUMNS, load_nsl_kdd


def test_browser_workspace_copies_only_safe_sources_and_assets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "streamlit_app.py").write_text("# Original app\n", encoding="utf-8")
    (source / "src").mkdir()
    (source / "src/runtime.py").write_text("# Original runtime\n", encoding="utf-8")
    (source / "src/__pycache__").mkdir()
    (source / ".streamlit").mkdir()
    (source / ".streamlit/config.toml").write_text("[theme]\nbase='dark'\n", encoding="utf-8")
    (source / ".streamlit/secrets.toml").write_text("secret='do-not-copy'\n", encoding="utf-8")
    (source / "static/fonts").mkdir(parents=True)
    (source / "static/fonts/license.txt").write_text("license", encoding="utf-8")
    for folder in ("models", "state", "data"):
        (source / folder).mkdir()
        (source / folder / "private.txt").write_text("do-not-copy", encoding="utf-8")
    workspace = support.prepare_workspace(tmp_path / "application", source)
    assert (workspace.root / "streamlit_app.py").read_bytes() == (
        source / "streamlit_app.py"
    ).read_bytes()
    assert (workspace.root / "src/runtime.py").read_bytes() == (
        source / "src/runtime.py"
    ).read_bytes()
    assert not (workspace.root / "src/__pycache__").exists()
    assert not (workspace.root / ".streamlit/secrets.toml").exists()
    assert not (workspace.root / "state").exists()
    assert not (workspace.root / "models").exists()
    assert not (workspace.root / "data/private.txt").exists()
    assert (workspace.root / "static/browser-owner.txt").read_text() == workspace.owner
    with pytest.raises(FileExistsError):
        support.prepare_workspace(workspace.root, source)


def test_browser_fixture_has_five_train_families_and_bounded_replay(tmp_path):
    workspace = support.prepare_workspace(tmp_path / "application")
    dataset = load_nsl_kdd(
        workspace.root / "data/KDDTrain+.txt", workspace.root / "data/KDDTest+.txt"
    )
    assert len(dataset.train) == 80
    assert set(dataset.train.attack_family) == {"normal", "dos", "probe", "r2l", "u2r"}
    assert dataset.test.attack_family.tolist() == ["dos", "dos", "normal"]
    assert all(set(record) == set(MODEL_INPUT_COLUMNS) for record in workspace.records.values())
    assert pd.read_csv(workspace.root / "data/KDDTest+.txt", names=NSL_KDD_COLUMNS).shape == (3, 43)
    support.write_manifest(workspace, tmp_path / "fixture.json")
    manifest = json.loads((tmp_path / "fixture.json").read_text())
    assert manifest["provider"] == "template"
    assert len(manifest["sha256"]["data/KDDTrain+.txt"]) == 64
    assert "not a quality benchmark" in manifest["fixture"]


def test_browser_environment_ignores_provider_and_global_config_overrides(tmp_path, monkeypatch):
    workspace = support.prepare_workspace(tmp_path / "application")
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ABUSEIPDB_API_KEY", "SOC_API_KEY"):
        monkeypatch.setenv(key, "do-not-forward")
    monkeypatch.setenv("SOC_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SOC_ENABLE_THREAT_INTEL", "true")
    monkeypatch.setenv("STREAMLIT_SERVER_PORT", "8501")
    env = support.child_environment(workspace)
    assert env["SOC_LLM_PROVIDER"] == "template"
    assert env["SOC_ENABLE_THREAT_INTEL"] == "false"
    assert env["PYTHONPATH"] == str(workspace.root)
    assert env["HOME"] == env["USERPROFILE"] == str(workspace.root / "home")
    assert "STREAMLIT_SERVER_PORT" not in env
    assert "SOC_API_KEY" not in env
    assert all(
        key not in env for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ABUSEIPDB_API_KEY")
    )


def test_browser_server_refuses_exited_process_even_if_port_is_healthy(monkeypatch):
    process = Mock(returncode=1)
    process.poll.return_value = 1
    opener = Mock()
    monkeypatch.setattr(support, "urlopen", opener)
    with pytest.raises(RuntimeError, match="exited with code 1"):
        support.wait_for_server(process, "http://127.0.0.1:1234", "owner")
    opener.assert_not_called()


def test_browser_server_does_not_accept_someone_elses_health_check(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    response = Mock()
    response.__enter__ = Mock(return_value=Mock(read=Mock(return_value=b"another-app")))
    response.__exit__ = Mock(return_value=False)
    opener = Mock(return_value=response)
    monkeypatch.setattr(support, "urlopen", opener)
    monkeypatch.setattr(support.time, "monotonic", Mock(side_effect=[0, 0, 2]))
    monkeypatch.setattr(support.time, "sleep", Mock())
    with pytest.raises(TimeoutError, match="isolated Streamlit"):
        support.wait_for_server(process, "http://127.0.0.1:1234", "owner", timeout=1)
    assert opener.call_args.args[0].endswith("/app/static/browser-owner.txt")
