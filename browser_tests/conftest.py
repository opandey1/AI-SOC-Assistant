"""Use the official Playwright pytest fixtures with an isolated app per test."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from browser_tests.support import (
    AppWorkspace,
    prepare_workspace,
    start_server,
    stop_server,
    wait_for_review_database,
    write_manifest,
)


@dataclass(frozen=True)
class Console:
    page: Page
    workspace: AppWorkspace
    artifacts: Path

    def screenshot(self, name: str, selector: str | None = None) -> None:
        target = self.page.locator(selector) if selector else self.page
        target.screenshot(path=str(self.artifacts / f"{name}.png"))


@pytest.fixture
def case_artifacts(request) -> Path:
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", request.node.name)
    path = Path(request.config.getoption("output")).resolve() / "console" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def app_server(tmp_path, case_artifacts):
    workspace = prepare_workspace(tmp_path / "application")
    write_manifest(workspace, case_artifacts / "fixture.json")
    process, url = start_server(workspace, case_artifacts / "server.log")
    try:
        yield workspace, url
    finally:
        stop_server(process)


@pytest.fixture
def console(page: Page, app_server, case_artifacts, width):
    workspace, url = app_server
    errors, outbound = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def local_only(route):
        if urlsplit(route.request.url).netloc == urlsplit(url).netloc:
            route.continue_()
        else:
            outbound.append(route.request.url)
            route.abort()

    page.context.route("**/*", local_only)
    page.set_viewport_size({"width": width, "height": 1100})
    page.set_default_timeout(30_000)
    expect.set_options(timeout=30_000)
    page.goto(url, wait_until="domcontentloaded")
    expect(page.get_by_text("No connection scored yet", exact=True)).to_be_visible()
    wait_for_review_database(page, workspace.database)
    expect(page.get_by_role("combobox", name="Ticket provider", exact=True)).to_have_value(
        "template"
    )
    if width < 600:
        collapse = page.get_by_role("button", name="Collapse sidebar", exact=True)
        if collapse.is_visible():
            collapse.click()
    else:
        expand = page.get_by_role("button", name="Expand sidebar", exact=True)
        if expand.is_visible():
            expand.click()
    try:
        yield Console(page, workspace, case_artifacts)
    finally:
        report = {"width": width, "page_errors": errors, "blocked_outbound_requests": outbound}
        (case_artifacts / "browser.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        expect(page.locator('[data-testid="stException"]')).to_have_count(0)
        assert not errors, f"Browser JavaScript errors: {errors}"
        assert not outbound, f"Unexpected external browser requests: {outbound}"
