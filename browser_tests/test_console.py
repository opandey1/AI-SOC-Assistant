"""Real deterministic console flows and layout contracts, not pixel snapshots."""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.parametrize("width", [1440, 1024, 390, 320])


def select(page, name, *, scope=None):
    option = (scope or page).get_by_role("radio", name=re.compile(rf"^{re.escape(name)}(?:$|\s)"))
    option.click()
    expect(option).to_have_attribute("aria-checked", "true")


def disposition(page, name):
    group = page.locator(".st-key-review_disposition")
    group.get_by_text(name, exact=True).click()
    expect(group.get_by_role("radio", name=re.compile(rf"^{re.escape(name)}\s"))).to_be_checked()


def settled(page):
    expect(page.locator('[data-stale="true"]')).to_have_count(0)
    page.evaluate("() => document.fonts.ready")


def open_governance(page):
    page.get_by_text("Feedback governance", exact=True).click()
    expect(page.get_by_role("button", name="Download cohort audit", exact=True)).to_be_visible()
    page.wait_for_function(
        """() => {
            const button = [...document.querySelectorAll('button')].find(el => el.textContent.includes('Download cohort audit'));
            const expander = button?.closest('[data-testid="stExpander"]');
            if (!expander) return false;
            return expander.getBoundingClientRect().bottom >= button.getBoundingClientRect().bottom;
        }"""
    )


def analyze(page, *, alert):
    context = page.get_by_text(re.compile(r"^Last scored event:"))
    previous = context.text_content() if context.count() else None
    page.locator(".st-key-triage_submit button").click()
    page.wait_for_function(
        "previous => [...document.querySelectorAll('[data-testid=stCaptionContainer]')].some(el => el.textContent.startsWith('Last scored event:') && el.textContent !== previous)",
        arg=previous,
        timeout=60_000,
    )
    expect(page.locator(".soc-verdict")).to_contain_text(
        "REQUIRES ANALYST VALIDATION" if alert else "NO TICKET GENERATED"
    )
    expect(page.get_by_text("Analysis complete", exact=True)).to_be_visible()
    settled(page)


def rows(console, query):
    with sqlite3.connect(console.workspace.database.as_uri() + "?mode=ro", uri=True) as connection:
        return connection.execute(query).fetchall()


def layout(console, name):
    page = console.page
    settled(page)
    geometry = page.evaluate(
        """() => {
            const rect = el => el.getBoundingClientRect().toJSON();
            const main = document.querySelector('[data-testid=stMain]');
            const header = document.querySelector('.soc-topbar');
            const brand = document.querySelector('.soc-brand');
            const elements = [...document.querySelectorAll('.soc-score, .soc-evidence-row, .soc-proto, .st-key-review_corrected_class button')];
            return {width: innerWidth, documentWidth: document.documentElement.scrollWidth,
                mainWidth: main.clientWidth, mainScrollWidth: main.scrollWidth,
                header: rect(header), brand: rect(brand),
                elements: elements.map(el => ({box: rect(el), client: el.clientWidth, scroll: el.scrollWidth}))};
        }"""
    )
    assert geometry["documentWidth"] <= geometry["width"]
    assert geometry["mainScrollWidth"] <= geometry["mainWidth"] + 1
    assert geometry["brand"]["right"] <= geometry["header"]["right"] + 1
    if geometry["width"] >= 1024:
        assert geometry["header"]["left"] >= 300, "Desktop sidebar must be expanded"
    for element in geometry["elements"]:
        assert element["box"]["width"] > 0
        assert element["box"]["right"] <= geometry["width"] + 1
        assert element["box"]["left"] >= 0
        assert element["scroll"] <= element["client"] + 1
    (console.artifacts / f"{name}-geometry.json").write_text(
        json.dumps(geometry, indent=2), encoding="utf-8"
    )
    console.screenshot(name)


def select_ticket(page, index=0):
    # Streamlit's pinned canvas grid has a 35 px row pitch and a header above it.
    page.locator('[data-testid="stDataFrame"] .dvn-scroller').click(
        position={"x": 16, "y": 52 + 35 * index}
    )
    expect(page.locator(".st-key-review_disposition")).to_be_visible()
    settled(page)


def test_triage_json_validation_evidence_and_download(console):
    page = console.page
    layout(console, "empty")
    expect(page.locator(".soc-empty-icon .material-symbols-rounded")).to_have_text("search_check")
    page.get_by_role("spinbutton", name="Test row", exact=True).fill("2")
    analyze(page, alert=False)
    expect(page.get_by_text("Why this was cleared", exact=True)).to_be_visible()
    expect(page.locator('[data-testid="stDownloadButton"]')).to_have_count(0)
    assert rows(console, "SELECT COUNT(*) FROM tickets") == [(0,)]
    assert page.locator(".soc-evidence-row").count() > 0
    layout(console, "dataset-clear")
    console.screenshot("cleared-evidence", ".soc-card")

    select(page, "JSON record")
    editor = page.get_by_role("textbox", name="Connection JSON", exact=True)
    expect(editor).to_be_visible()
    for payload, message in (
        ("{", "Invalid connection input"),
        ("[]", "must be an object"),
        ("{}", "missing required connection fields"),
    ):
        editor.fill(payload)
        page.locator(".st-key-triage_submit button").click()
        expect(page.get_by_text(re.compile(message))).to_be_visible()
        expect(editor).to_have_value(payload)
        expect(page.locator(".soc-verdict")).to_have_count(0)
    layout(console, "json-invalid")

    invalid = {**console.workspace.records["normal"], "duration": "NaN"}
    editor.fill(json.dumps(invalid))
    page.locator(".st-key-triage_submit button").click()
    expect(
        page.get_by_text(re.compile("Required feature values must be scalar and finite"))
    ).to_be_visible()
    expect(page.locator(".soc-verdict")).to_have_count(0)

    editor.fill(json.dumps(console.workspace.records["normal"], indent=2))
    analyze(page, alert=False)
    assert rows(console, "SELECT COUNT(*) FROM tickets") == [(0,)]
    editor.fill(json.dumps(console.workspace.records["dos"], indent=2))
    analyze(page, alert=True)
    expect(page.get_by_text("Why this was flagged", exact=True)).to_be_visible()
    expect(
        page.get_by_text("SHAP evidence for Random Forest class: dos.", exact=True)
    ).to_be_visible()
    with page.expect_download() as download:
        page.locator('[data-testid="stDownloadButton"] button').click()
    destination = console.artifacts / "incident-ticket.md"
    download.value.save_as(destination)
    assert "Incident Summary" in destination.read_text(encoding="utf-8")
    assert rows(console, "SELECT source, predicted_class FROM tickets") == [
        ("dashboard-json", "dos")
    ]
    assert rows(console, "SELECT COUNT(*) FROM reviews") == [(0,)]
    layout(console, "json-alert")
    console.screenshot("flagged-evidence", ".soc-card")
    select(page, "Dataset row")
    expect(page.get_by_role("spinbutton", name="Test row", exact=True)).to_be_visible()
    select(page, "JSON record")
    expect(editor).to_have_value(json.dumps(console.workspace.records["dos"], indent=2))


def test_replay_feed_retention_and_end_of_file(console):
    page = console.page
    select(page, "Live replay")
    expect(page.get_by_role("spinbutton", name="Start row", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="Source IP", exact=True)).to_have_count(0)
    page.get_by_role("spinbutton", name="Events", exact=True).fill("3")
    page.get_by_role("spinbutton", name="Interval (seconds)", exact=True).fill("0")
    page.locator(".st-key-triage_submit button").click()
    expect(page.get_by_text("Replay complete: 3 of 3 requested", exact=True)).to_be_visible(
        timeout=60_000
    )
    expect(page.get_by_text("3 processed / 3 requested", exact=True)).to_be_visible()
    expect(page.locator(".soc-verdict")).to_contain_text("NO TICKET GENERATED")
    expect(page.locator('[data-testid="stDataFrame"]')).to_have_count(1)
    assert rows(
        console,
        "SELECT source, predicted_class, COUNT(*) FROM tickets GROUP BY source, predicted_class",
    ) == [("nsl-kdd-replay", "dos", 2)]
    layout(console, "replay")
    console.screenshot("replay-feed", '[data-testid="stDataFrame"]')
    select(page, "JSON record")
    expect(page.get_by_role("textbox", name="Connection JSON", exact=True)).to_be_visible()
    expect(page.get_by_text("3 processed / 3 requested", exact=True)).to_be_visible()
    select(page, "Live replay")
    page.get_by_role("spinbutton", name="Start row", exact=True).fill("2")
    page.get_by_role("spinbutton", name="Events", exact=True).fill("5")
    page.locator(".st-key-triage_submit button").click()
    expect(page.get_by_text("Replay complete: 1 of 5 requested", exact=True)).to_be_visible()
    expect(page.get_by_text("1 processed / 5 requested", exact=True)).to_be_visible()
    expect(page.locator(".soc-verdict")).to_contain_text("NO TICKET GENERATED")
    assert rows(console, "SELECT COUNT(*) FROM tickets") == [(2,)]
    layout(console, "replay-end-of-file")


def test_review_validation_save_and_model_cohort(console):
    page = console.page
    select(page, "Review queue")
    expect(page.get_by_text("No ticket selected", exact=True)).to_be_visible()
    expect(page.get_by_text("No tickets match this review state", exact=True)).to_be_visible()
    layout(console, "review-empty")
    select(page, "Triage")
    expect(page.get_by_role("spinbutton", name="Test row", exact=True)).to_be_visible()
    analyze(page, alert=True)
    analyze(page, alert=True)
    select(page, "Review queue")
    expect(page.get_by_text("No ticket selected", exact=True)).to_be_visible()
    select_ticket(page)
    save = page.locator('.st-key-review_detail [data-testid="stFormSubmitButton"] button')
    pills = page.locator(".st-key-review_corrected_class")
    expect(save).to_be_disabled()
    expect(pills.get_by_role("radio", name="NORMAL", exact=True)).to_be_disabled()
    disposition(page, "False positive")
    expect(pills.get_by_role("radio", name="NORMAL", exact=True)).to_be_enabled()
    save.click()
    expect(
        page.get_by_text("Enter an analyst name before recording a review.", exact=True)
    ).to_be_visible()
    assert rows(console, "SELECT COUNT(*) FROM reviews") == [(0,)]
    normal = pills.get_by_role("radio", name="NORMAL", exact=True)
    normal.focus()
    page.keyboard.press("ArrowRight")
    expect(pills.get_by_role("radio", name="DOS", exact=True)).to_be_focused()
    page.keyboard.press("Space")
    expect(pills.get_by_role("radio", name="DOS", exact=True)).to_have_attribute(
        "aria-checked", "true"
    )
    select(page, "U2R", scope=pills)
    page.get_by_role("textbox", name="Analyst", exact=True).fill("browser-regression-analyst")
    page.get_by_role("textbox", name="Notes", exact=True).fill(
        "Synthetic UI regression; not real incident feedback."
    )
    layout(console, "review-correction")
    console.screenshot("review-form", ".st-key-review_detail")
    save.click()
    expect(page.get_by_text("No ticket selected", exact=True)).to_be_visible()
    select(page, "All", scope=page.locator(".st-key-review_state"))
    expect(page.get_by_text("No ticket selected", exact=True)).to_be_visible()
    select_ticket(page, index=1)
    expect(page.get_by_role("textbox", name="Analyst", exact=True)).to_have_value("")
    expect(page.get_by_role("textbox", name="Notes", exact=True)).to_have_value("")
    expect(save).to_be_disabled()
    disposition(page, "Confirmed attack")
    expect(normal).to_be_disabled()
    page.get_by_role("textbox", name="Analyst", exact=True).fill("second-regression-analyst")
    save.click()
    expect(
        page.locator(".st-key-review_detail").get_by_text("confirmed attack", exact=True)
    ).to_be_visible()
    assert rows(
        console, "SELECT disposition, corrected_class, reviewed_by FROM reviews ORDER BY id"
    ) == [
        ("false_positive", "u2r", "browser-regression-analyst"),
        ("confirmed_attack", None, "second-regression-analyst"),
    ]
    select(page, "Needs investigation", scope=page.locator(".st-key-review_state"))
    expect(page.get_by_text("No tickets match this review state", exact=True)).to_be_visible()
    expect(page.get_by_text("No ticket selected", exact=True)).to_be_visible()
    select(page, "Model")
    expect(page.get_by_text("No consensus-approved cohort yet", exact=True)).to_be_visible()
    expect(page.locator(".st-key-retrain_model button")).to_be_disabled()
    open_governance(page)
    expect(page.get_by_text("1 awaiting agreement", exact=False)).to_be_visible()
    layout(console, "model-cohort")
    console.screenshot("cohort", ".st-key-model_operations")
    assert not (console.workspace.root / "models/soc_model.joblib").exists()
    assert not (console.workspace.root / "state/retrain_report.json").exists()


def test_model_protocol_layout_fonts_and_disabled_retrain(console):
    page = console.page
    select(page, "Model")
    expect(page.get_by_role("heading", name="Model operations", exact=True)).to_be_visible()
    expect(page.locator(".st-key-retrain_model button")).to_be_disabled()
    expect(page.get_by_text("No consensus-approved cohort yet", exact=True)).to_be_visible()
    expect(page.locator(".soc-proto")).to_have_count(3)
    settled(page)
    assert page.evaluate(
        "() => document.fonts.check('14px Inter') && document.fonts.check('13px \"JetBrains Mono\"')"
    )
    fonts = page.evaluate(
        "() => [...document.fonts].map(face => ({family: face.family, status: face.status}))"
    )
    for family in ("Inter", "JetBrains Mono"):
        assert any(family in font["family"] and font["status"] == "loaded" for font in fonts)
    values = page.locator(".soc-proto .soc-track").evaluate_all(
        "tracks => tracks.map(track => ({ratio: track.firstElementChild.getBoundingClientRect().width / track.getBoundingClientRect().width, height: track.getBoundingClientRect().height}))"
    )
    assert len(values) == 3
    for track, expected in zip(values, [0.5889, 0.7440, 0.9988]):
        assert track["ratio"] == pytest.approx(expected, abs=0.002)
        assert track["height"] == 7
    layout(console, "model-protocols")
    console.screenshot("protocol-panel", ".st-key-evaluation_protocols")
    assert rows(console, "SELECT COUNT(*) FROM reviews") == [(0,)]
