"""Real local candidate training, promotion and rollback; synthetic, not quality evidence."""

import sqlite3

import pytest
from playwright.sync_api import expect

from browser_tests.test_console import (
    analyze,
    disposition,
    layout,
    open_governance,
    rows,
    select,
    select_ticket,
)

pytestmark = [
    pytest.mark.candidate_evaluation,
    pytest.mark.parametrize("width", [1440, 1024, 390, 320]),
]


def selection(console):
    database = console.workspace.root / "models/registry/registry.sqlite"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        return connection.execute("SELECT active, previous, generation FROM selection").fetchone()


def test_real_candidate_promotion_and_rollback(console):
    page = console.page
    for _ in range(5):
        analyze(page, alert=True)
    select(page, "Review queue")
    for _ in range(5):
        select_ticket(page)
        disposition(page, "False positive")
        select(page, "DOS", scope=page.locator(".st-key-review_corrected_class"))
        page.get_by_role("textbox", name="Analyst", exact=True).fill("synthetic-browser-analyst")
        page.locator('.st-key-review_detail [data-testid="stFormSubmitButton"] button').click()
        expect(page.get_by_text("No ticket selected", exact=True)).to_be_visible()
    assert rows(console, "SELECT COUNT(*) FROM reviews") == [(5,)]
    select(page, "Model")
    expect(page.locator(".st-key-retrain_model button")).to_be_disabled()
    open_governance(page)
    expect(page.get_by_text("5 awaiting agreement", exact=False)).to_be_visible()
    layout(console, "awaiting-second-review")
    select(page, "Review queue")
    select(page, "All", scope=page.locator(".st-key-review_state"))
    for index in range(5):
        select_ticket(page, index=index)
        expect(
            page.locator(".st-key-review_detail").get_by_text(f"Ticket #{5-index}", exact=True)
        ).to_be_visible()
        disposition(page, "False positive")
        select(page, "DOS", scope=page.locator(".st-key-review_corrected_class"))
        page.get_by_role("textbox", name="Analyst", exact=True).fill("second-browser-analyst")
        page.locator('.st-key-review_detail [data-testid="stFormSubmitButton"] button').click()
        expect(
            page.locator(".st-key-review_detail").get_by_text(f"review-{6+index}", exact=True)
        ).to_be_visible()
    assert rows(console, "SELECT COUNT(*) FROM reviews") == [(10,)]
    select(page, "Model")
    expect(page.locator(".st-key-retrain_model button")).to_be_enabled()
    select(page, "Review queue")
    select(page, "All", scope=page.locator(".st-key-review_state"))
    select_ticket(page)
    disposition(page, "False positive")
    select(page, "NORMAL", scope=page.locator(".st-key-review_corrected_class"))
    page.get_by_role("textbox", name="Analyst", exact=True).fill("second-browser-analyst")
    page.locator('.st-key-review_detail [data-testid="stFormSubmitButton"] button').click()
    expect(
        page.locator(".st-key-review_detail").get_by_text("review-11", exact=True)
    ).to_be_visible()
    select(page, "Model")
    expect(page.locator(".st-key-retrain_model button")).to_be_disabled()
    open_governance(page)
    expect(page.get_by_text("1 conflicting", exact=False)).to_be_visible()
    layout(console, "conflict-held-out")
    select(page, "Review queue")
    select(page, "All", scope=page.locator(".st-key-review_state"))
    select_ticket(page)
    disposition(page, "False positive")
    select(page, "DOS", scope=page.locator(".st-key-review_corrected_class"))
    page.get_by_role("textbox", name="Analyst", exact=True).fill("second-browser-analyst")
    page.locator('.st-key-review_detail [data-testid="stFormSubmitButton"] button').click()
    expect(
        page.locator(".st-key-review_detail").get_by_text("review-12", exact=True)
    ).to_be_visible()
    select(page, "Model")
    expect(page.locator(".st-key-retrain_model button")).to_be_enabled()
    page.locator(".st-key-retrain_model button").click()
    expect(page.get_by_text("Candidate saved; active model unchanged", exact=True)).to_be_visible(
        timeout=60_000
    )
    expect(
        page.get_by_text(
            "Evaluation non-regression check passed; not yet production certification.", exact=True
        )
    ).to_be_visible()
    assert selection(console) == (None, None, 0)
    assert not (console.workspace.root / "models/soc_model.joblib").exists()
    promote = page.locator(".st-key-promote_candidate button")
    rollback = page.locator(".st-key-rollback_model button")
    expect(promote).to_be_disabled()
    confirmation = page.get_by_role("checkbox", name="Confirm active-model change", exact=True)
    confirmation.focus()
    page.keyboard.press("Space")
    expect(confirmation).to_be_checked()
    expect(promote).to_be_disabled()
    page.get_by_role("textbox", name="Operator", exact=True).fill("browser-model-operator")
    page.get_by_role("textbox", name="Operator", exact=True).press("Tab")
    expect(promote).to_be_enabled()
    layout(console, "candidate-ready")
    promote.click()
    expect(
        page.get_by_role("checkbox", name="Confirm active-model change", exact=True)
    ).not_to_be_checked()
    active, previous, generation = selection(console)
    assert active and previous is None and generation == 1
    expect(promote).to_be_disabled()
    page.get_by_text("Confirm active-model change", exact=True).click()
    expect(confirmation).to_be_checked()
    expect(rollback).to_be_enabled()
    layout(console, "candidate-promoted")
    rollback.click()
    expect(
        page.get_by_role("checkbox", name="Confirm active-model change", exact=True)
    ).not_to_be_checked()
    assert selection(console) == (None, active, 2)
    layout(console, "candidate-rollback")
    with sqlite3.connect(
        (console.workspace.root / "models/registry/registry.sqlite").as_uri() + "?mode=ro", uri=True
    ) as connection:
        assert connection.execute("SELECT action, actor FROM actions ORDER BY id").fetchall() == [
            ("promote", "browser-model-operator"),
            ("rollback", "browser-model-operator"),
        ]
    assert rows(console, "SELECT COUNT(*) FROM reviews") == [(12,)]
