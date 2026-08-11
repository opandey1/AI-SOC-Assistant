"""Tests for the analyst console design system."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src import ui

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP = PROJECT_ROOT / "streamlit_app.py"


def _ui_attributes_used_by_app() -> set[str]:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ui"
    }


def test_every_ui_symbol_used_by_the_app_exists():
    """Guards against renaming a helper and leaving a stale call site behind.

    ``ui.card_close()`` survived a refactor once and only raised at runtime, on the
    branch that renders a completed analysis. Neither black, flake8 nor a smoke run
    of the empty screens caught it.
    """

    missing = sorted(name for name in _ui_attributes_used_by_app() if not hasattr(ui, name))
    assert not missing, f"streamlit_app.py references undefined ui symbols: {missing}"


@pytest.mark.parametrize(
    "payload",
    ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "a & b < c"],
)
def test_rendered_helpers_escape_untrusted_text(payload):
    """Feature names, IPs and event ids reach the DOM and are attacker-influenced."""

    rendered = "".join(
        [
            ui.pill(payload, ui.TOKENS["accent"]),
            ui.tile(payload, payload, payload),
            ui.callout(payload, payload, ui.TOKENS["status-ok"]),
            ui.kv_block([(payload, payload, None)]),
            ui.section_label(payload),
            ui.empty_state(ui.ICON_SCAN, payload, payload),
            ui.card(payload, payload, ""),
            ui.panel_header(payload, payload),
            ui.protocol_card(payload, payload, 0.5, 0.5, ui.TOKENS["accent"], payload),
            ui.evidence_rows(
                [{"feature": payload, "true_value": payload, "shap_value": 1.0, "direction": "s"}]
            ),
        ]
    )
    # The payload must never survive as live markup. Escaped text such as
    # "onerror=" is inert, so assert on tag delimiters rather than substrings.
    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "&lt;" in rendered or "&amp;" in rendered or "&quot;" in rendered


@pytest.mark.parametrize(
    "ratio,expected",
    [(-1.0, "0"), (0.0, "0"), (0.5, "50"), (1.0, "100"), (4.2, "100"), (float("nan"), "0")],
)
def test_track_clamps_ratio_into_range(ratio, expected):
    assert f"width:{expected}" in ui.track(ratio, ui.TOKENS["accent"]).replace(".0%", "%")


def test_verdict_card_reports_alert_and_clear_states():
    common = dict(
        fused_confidence=0.887,
        rf_confidence=1.0,
        isolation_risk=0.717,
        isolation_score=-0.0877,
        isolation_threshold=0.7,
        alert_reason="both",
    )
    alert = ui.verdict_card(predicted_class="dos", is_alert=True, **common)
    clear = ui.verdict_card(predicted_class="normal", is_alert=False, **common)
    assert "REQUIRES ANALYST VALIDATION" in alert
    assert "NO TICKET GENERATED" in clear
    assert ui.FAMILY_COLORS["dos"] in alert
    assert ui.FAMILY_COLORS["normal"] in clear


def test_evidence_rows_marks_direction_and_handles_empty():
    drivers = [
        {"feature": "flag_S0", "true_value": 1.0, "shap_value": 0.2, "direction": "supports x"},
        {"feature": "src_bytes", "true_value": 0.0, "shap_value": -0.1, "direction": "opposes x"},
        {"feature": "land", "true_value": 0.0, "shap_value": 0.0, "direction": "is neutral for x"},
    ]
    rendered = ui.evidence_rows(drivers)
    assert "SUPPORTS" in rendered and "OPPOSES" in rendered and "NEUTRAL" in rendered
    assert "No SHAP drivers" in ui.evidence_rows([])


def test_family_color_falls_back_for_unknown_class():
    assert ui.family_color("dos") == ui.FAMILY_COLORS["dos"]
    assert ui.family_color("NOT_A_CLASS") == ui.FAMILY_COLORS["unknown"]


def test_css_defines_every_token_and_protects_the_icon_font():
    import re

    for name in ui.TOKENS:
        assert f"--soc-{name}:" in ui.CSS

    # A broad [class*="st-"] selector also matches Streamlit's Material Symbols
    # spans and renders every icon as its literal ligature name. Strip /* */
    # comments first so the warning note about it does not trip the check.
    stylesheet = re.sub(r"/\*.*?\*/", "", ui.CSS, flags=re.DOTALL)
    assert '[class*="st-"]' not in stylesheet
    assert "Material Symbols Rounded" in stylesheet
