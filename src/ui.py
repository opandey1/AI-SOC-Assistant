"""Dark SOC console design system for the Streamlit analyst UI.

All rendered values are HTML-escaped before interpolation. Connection records,
source IPs and SHAP feature names originate from dataset rows or analyst input,
so they are treated as untrusted text rather than markup.
"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------
# Tokens — mirrors the "SOC Tokens" Figma variable collection.
# --------------------------------------------------------------------------

TOKENS: dict[str, str] = {
    "bg-canvas": "#0A0D13",
    "bg-surface": "#10141C",
    "bg-elevated": "#171C26",
    "bg-inset": "#0D1117",
    "border-subtle": "#232A36",
    "border-default": "#2E3746",
    "border-strong": "#3D4859",
    "text-primary": "#E8ECF3",
    "text-secondary": "#9BA6B8",
    "text-tertiary": "#6B7688",
    "accent": "#7C6CF6",
    "accent-hover": "#9B8DF9",
    "status-alert": "#F0453A",
    "status-warn": "#F59E0B",
    "status-ok": "#10B981",
    "status-info": "#22D3EE",
}

FAMILY_COLORS: dict[str, str] = {
    "normal": "#10B981",
    "dos": "#F0453A",
    "probe": "#F59E0B",
    "r2l": "#22D3EE",
    "u2r": "#EC4899",
    "unknown": "#6B7688",
}

DISPOSITION_COLORS: dict[str, str] = {
    "confirmed_attack": "#F0453A",
    "false_positive": "#22D3EE",
    "needs_investigation": "#9BA6B8",
    "unreviewed": "#F59E0B",
}

_FONT_STACK = (
    'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
)
_MONO_STACK = '"JetBrains Mono", "SF Mono", SFMono-Regular, Menlo, Consolas, monospace'


def _token_block() -> str:
    lines = [f"    --soc-{name}: {value};" for name, value in TOKENS.items()]
    lines.append(f"    --soc-font: {_FONT_STACK};")
    lines.append(f"    --soc-mono: {_MONO_STACK};")
    return "\n".join(lines)


CSS = f"""
<style>
:root {{
{_token_block()}
}}

/* Scope the body font to the app root and let it inherit. Never use a broad
   attribute selector such as [class*="st-"] here: it also matches Streamlit's
   Material Symbols icon spans, which replaces the icon font with a text font
   and renders every icon as its literal ligature name ("play_arrow"). */
html, body, .stApp {{
    font-family: var(--soc-font);
}}
.stApp {{ background: var(--soc-bg-canvas); }}

/* Icon fonts must win over any inherited family. */
[data-testid="stIconMaterial"],
span.material-icons,
span.material-icons-outlined,
span[class*="material-symbols"],
.stApp [class*="material-symbols"] {{
    font-family: "Material Symbols Rounded", "Material Symbols Outlined",
                 "Material Icons" !important;
    font-weight: normal !important;
    letter-spacing: normal !important;
    font-feature-settings: "liga" !important;
}}

/* Reclaim the space Streamlit reserves for its own chrome. */
.block-container {{ padding-top: 1.1rem; padding-bottom: 2.5rem; max-width: 1560px; }}
#MainMenu, footer,
header [data-testid="stStatusWidget"],
[data-testid="stAppDeployButton"],
[data-testid="stToolbarActions"] {{ display: none !important; }}
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}

section[data-testid="stSidebar"] {{
    background: var(--soc-bg-surface);
    border-right: 1px solid var(--soc-border-subtle);
}}
section[data-testid="stSidebar"] .block-container {{ padding-top: 1.4rem; }}

code, pre, kbd, samp {{ font-family: var(--soc-mono) !important; }}

/* ---------------- Top bar ---------------- */
.soc-topbar {{
    display: flex; align-items: center; gap: 14px;
    padding: 12px 18px; margin: -0.4rem 0 18px 0;
    background: var(--soc-bg-surface);
    border: 1px solid var(--soc-border-subtle);
    border-radius: 12px;
}}
.soc-brand {{ display: flex; align-items: center; gap: 9px; }}
.soc-brand-name {{
    font-size: 14px; font-weight: 600; letter-spacing: .2px;
    color: var(--soc-text-primary);
}}
.soc-brand-sub {{
    font-size: 11px; font-weight: 500; letter-spacing: 1.2px;
    color: var(--soc-text-tertiary);
}}
.soc-topbar-spacer {{ flex: 1; }}
.soc-runtime {{ display: flex; align-items: center; gap: 10px; }}
.soc-mono-note {{ font-family: var(--soc-mono); font-size: 12px; color: var(--soc-text-tertiary); }}

/* ---------------- Generic chrome ---------------- */
.soc-pill {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px 4px 9px; border-radius: 999px;
    font-size: 10px; font-weight: 600; letter-spacing: .7px;
}}
.soc-dot {{ width: 7px; height: 7px; border-radius: 50%; flex: none; }}
.soc-section-label {{
    font-size: 10px; font-weight: 600; letter-spacing: 1.1px;
    color: var(--soc-text-tertiary); margin: 2px 0 8px 0;
}}
.soc-card {{
    background: var(--soc-bg-surface);
    border: 1px solid var(--soc-border-subtle);
    border-radius: 12px; padding: 16px 18px 18px 18px;
}}
.soc-card-title {{
    font-size: 15px; font-weight: 600; letter-spacing: -.1px;
    color: var(--soc-text-primary); margin-bottom: 2px;
}}
.soc-card-sub {{ font-size: 12px; color: var(--soc-text-tertiary); margin-bottom: 12px; }}
.soc-panel-header {{ margin: 2px 0 10px 0; }}
.soc-panel-header .soc-card-sub {{ margin-bottom: 0; }}

/* ---------------- Metric tiles ---------------- */
.soc-tile-row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.soc-tile {{
    flex: 1 1 150px; background: var(--soc-bg-surface);
    border: 1px solid var(--soc-border-subtle);
    border-radius: 10px; padding: 12px 15px 13px 15px;
}}
.soc-tile-label {{
    font-size: 10px; font-weight: 600; letter-spacing: .9px;
    color: var(--soc-text-tertiary);
}}
.soc-tile-value {{
    font-family: var(--soc-mono); font-size: 23px; font-weight: 500;
    letter-spacing: -.3px; line-height: 1.28; color: var(--soc-text-primary);
}}
.soc-tile-value.small {{ font-size: 14px; line-height: 1.7; }}
.soc-tile-note {{ font-size: 11px; color: var(--soc-text-secondary); }}

/* ---------------- Verdict card ---------------- */
.soc-verdict {{
    background: var(--soc-bg-surface);
    border: 1px solid var(--soc-border-subtle);
    border-radius: 12px; padding: 17px 20px 19px 18px;
}}
.soc-verdict-head {{ display: flex; align-items: center; gap: 13px; margin-bottom: 17px; }}
.soc-verdict-headline {{ margin-left: auto; text-align: right; }}
.soc-verdict-headline .k {{
    font-size: 10px; font-weight: 600; letter-spacing: .9px; color: var(--soc-text-tertiary);
}}
.soc-verdict-headline .v {{
    font-family: var(--soc-mono); font-size: 25px; font-weight: 500;
    letter-spacing: -.4px; line-height: 1.25; color: var(--soc-text-primary);
}}
.soc-scores {{ display: flex; gap: 15px; flex-wrap: wrap; }}
.soc-score {{
    flex: 1 1 210px; background: var(--soc-bg-elevated);
    border: 1px solid var(--soc-border-subtle);
    border-radius: 9px; padding: 11px 13px 12px 13px;
}}
.soc-score-top {{ display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }}
.soc-score-label {{
    font-size: 10px; font-weight: 600; letter-spacing: .9px;
    color: var(--soc-text-tertiary); flex: 1;
}}
.soc-score-value {{
    font-family: var(--soc-mono); font-size: 15px; font-weight: 500; letter-spacing: -.2px;
}}
.soc-score-note {{ font-size: 11px; color: var(--soc-text-secondary); margin-top: 7px; }}

/* ---------------- Bars ---------------- */
.soc-track {{
    height: 7px; border-radius: 4px; background: var(--soc-bg-inset);
    overflow: hidden; width: 100%;
}}
.soc-track > span {{ display: block; height: 100%; border-radius: 4px; }}

/* ---------------- Evidence rows ---------------- */
.soc-evidence-row {{ padding: 9px 0; border-bottom: 1px solid var(--soc-border-subtle); }}
.soc-evidence-row:last-child {{ border-bottom: none; }}
.soc-evidence-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
.soc-evidence-feature {{
    font-family: var(--soc-mono); font-size: 12px; font-weight: 500;
    color: var(--soc-text-primary); flex: 1; word-break: break-all;
}}
.soc-evidence-value {{
    font-family: var(--soc-mono); font-size: 12px; color: var(--soc-text-secondary);
}}
.soc-tag {{
    font-size: 9px; font-weight: 600; letter-spacing: .5px;
    padding: 2px 7px; border-radius: 4px; white-space: nowrap;
}}

/* ---------------- Callout ---------------- */
.soc-callout {{
    display: flex; gap: 10px; padding: 10px 12px;
    border-radius: 8px; margin-bottom: 4px;
}}
.soc-callout .rule {{ width: 3px; border-radius: 2px; flex: none; }}
.soc-callout .title {{ font-size: 12px; font-weight: 600; margin-bottom: 2px; }}
.soc-callout .body {{ font-size: 12px; color: var(--soc-text-secondary); }}

/* ---------------- Key/value ---------------- */
.soc-kv {{
    background: var(--soc-bg-inset); border: 1px solid var(--soc-border-subtle);
    border-radius: 8px; padding: 6px 12px;
}}
.soc-kv-row {{
    display: flex; align-items: center; gap: 10px; padding: 5px 0;
}}
.soc-kv-row + .soc-kv-row {{ border-top: 1px solid var(--soc-border-subtle); }}
.soc-kv-k {{ font-size: 12px; color: var(--soc-text-tertiary); flex: 1; }}
.soc-kv-v {{ font-family: var(--soc-mono); font-size: 12px; font-weight: 500; }}

/* ---------------- Protocol cards ---------------- */
.soc-proto {{
    background: var(--soc-bg-elevated); border: 1px solid var(--soc-border-subtle);
    border-radius: 10px; padding: 12px 14px 13px 14px; margin-bottom: 10px;
}}
.soc-proto-top {{ display: flex; align-items: center; gap: 10px; margin-bottom: 9px; }}
.soc-proto-name {{ font-size: 13px; font-weight: 600; color: var(--soc-text-primary); }}
.soc-proto-set {{ font-family: var(--soc-mono); font-size: 11px; color: var(--soc-text-tertiary); }}
.soc-proto-acc {{
    margin-left: auto; text-align: right; font-family: var(--soc-mono);
    font-size: 18px; font-weight: 500; letter-spacing: -.3px;
}}
.soc-proto-f1 {{ font-family: var(--soc-mono); font-size: 10px; color: var(--soc-text-tertiary); }}
.soc-proto-blurb {{ font-size: 11px; color: var(--soc-text-secondary); margin-top: 8px; }}

/* ---------------- Empty state ---------------- */
.soc-empty {{
    background: var(--soc-bg-surface);
    border: 1px dashed var(--soc-border-default);
    border-radius: 12px; padding: 40px 24px; text-align: center;
}}
.soc-empty-icon {{ margin-bottom: 10px; line-height: 0; }}
.soc-empty-title {{
    font-size: 14px; font-weight: 600; color: var(--soc-text-secondary); margin-bottom: 4px;
}}
.soc-empty-body {{
    font-size: 12px; color: var(--soc-text-tertiary);
    max-width: 460px; margin: 0 auto; line-height: 1.6;
}}

/* ---------------- Streamlit widget alignment ---------------- */
div[data-testid="stForm"] {{
    background: var(--soc-bg-surface);
    border: 1px solid var(--soc-border-subtle);
    border-radius: 12px; padding: 16px 18px;
}}
div[data-testid="stDataFrame"] {{ border-radius: 10px; }}
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
    border-radius: 8px; font-weight: 600; font-size: 13px;
}}
div[data-testid="stExpander"] details {{
    background: var(--soc-bg-surface);
    border: 1px solid var(--soc-border-subtle);
    border-radius: 10px;
}}
</style>
"""

_SHIELD = (
    '<svg width="21" height="21" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<path d="M12 2 4 5.5v6c0 5 3.4 9.3 8 10.5 4.6-1.2 8-5.5 8-10.5v-6L12 2Z" '
    'stroke="#7C6CF6" stroke-width="1.8" stroke-linejoin="round"/>'
    '<path d="M8.6 12.1l2.3 2.3 4.5-4.6" stroke="#7C6CF6" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _clamp(value: float) -> float:
    """Clamp a ratio into [0, 1], treating non-finite input as 0."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def _tint(hex_color: str, alpha: float) -> str:
    colour = hex_color.lstrip("#")
    red, green, blue = (int(colour[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def family_color(family: str) -> str:
    return FAMILY_COLORS.get(str(family).lower(), FAMILY_COLORS["unknown"])


def pill(text: str, color: str, *, dot: bool = True) -> str:
    """Return a coloured pill. `text` is escaped; `color` must be caller-controlled."""

    marker = f'<span class="soc-dot" style="background:{color}"></span>' if dot else ""
    return (
        f'<span class="soc-pill" style="background:{_tint(color, 0.16)};'
        f'border:1px solid {_tint(color, 0.40)};color:{color}">'
        f"{marker}{escape(str(text))}</span>"
    )


def family_chip(family: str) -> str:
    return pill(str(family).upper(), family_color(family))


def track(ratio: float, color: str, *, opacity: float = 1.0) -> str:
    width = round(_clamp(ratio) * 100, 2)
    fill = color if opacity >= 1.0 else _tint(color, opacity)
    return f'<div class="soc-track"><span style="width:{width}%;background:{fill}"></span></div>'


def section_label(text: str) -> str:
    return f'<div class="soc-section-label">{escape(str(text))}</div>'


def topbar(*, model_version: str, provider: str) -> str:
    offline = provider == "template"
    tone = TOKENS["status-ok"] if offline else TOKENS["status-info"]
    label = "OFFLINE MODE" if offline else f"{provider.upper()} PROVIDER"
    return (
        '<div class="soc-topbar">'
        f'<div class="soc-brand">{_SHIELD}'
        '<span class="soc-brand-name">AI-SOC</span>'
        '<span class="soc-brand-sub">ASSISTANT</span></div>'
        '<div class="soc-topbar-spacer"></div>'
        f'<div class="soc-runtime">{pill(label, tone)}'
        f'<span class="soc-mono-note">{escape(str(model_version))}</span></div>'
        "</div>"
    )


def tile(
    label: str, value: str, note: str = "", *, color: str | None = None, small: bool = False
) -> str:
    tone = color or TOKENS["text-primary"]
    size_class = " small" if small else ""
    note_html = f'<div class="soc-tile-note">{escape(str(note))}</div>' if note else ""
    return (
        '<div class="soc-tile">'
        f'<div class="soc-tile-label">{escape(str(label))}</div>'
        f'<div class="soc-tile-value{size_class}" style="color:{tone}">{escape(str(value))}</div>'
        f"{note_html}</div>"
    )


def tile_row(tiles: Iterable[str]) -> str:
    return f'<div class="soc-tile-row">{"".join(tiles)}</div>'


def callout(title: str, body: str, color: str) -> str:
    return (
        f'<div class="soc-callout" style="background:{_tint(color, 0.10)};'
        f'border:1px solid {_tint(color, 0.30)}">'
        f'<div class="rule" style="background:{color}"></div><div>'
        f'<div class="title" style="color:{color}">{escape(str(title))}</div>'
        f'<div class="body">{escape(str(body))}</div></div></div>'
    )


def kv_block(rows: Sequence[tuple[str, str, str | None]]) -> str:
    body = "".join(
        f'<div class="soc-kv-row"><span class="soc-kv-k">{escape(str(key))}</span>'
        f'<span class="soc-kv-v" style="color:{tone or TOKENS["text-primary"]}">'
        f"{escape(str(value))}</span></div>"
        for key, value, tone in rows
    )
    return f'<div class="soc-kv">{body}</div>'


def verdict_card(
    *,
    predicted_class: str,
    is_alert: bool,
    fused_confidence: float,
    rf_confidence: float,
    isolation_risk: float,
    isolation_score: float,
    isolation_threshold: float,
    alert_reason: str,
) -> str:
    """Render the hero verdict card. Score bars mirror the fusion rule in src.train."""

    tone = family_color(predicted_class)
    edge = TOKENS["status-alert"] if is_alert else TOKENS["status-ok"]
    state = (
        pill("REQUIRES ANALYST VALIDATION", TOKENS["status-alert"], dot=False)
        if is_alert
        else pill("NO TICKET GENERATED", TOKENS["status-ok"], dot=False)
    )

    scores = [
        (
            "RANDOM FOREST FAMILY",
            f"{rf_confidence:.1%}",
            rf_confidence,
            tone,
            f"Predicted class: {predicted_class}",
        ),
        (
            "ISOLATION FOREST RISK",
            f"{isolation_risk:.1%}",
            isolation_risk,
            TOKENS["status-warn"],
            f"Threshold {isolation_threshold:.1%} · raw {isolation_score:.6f}",
        ),
        (
            "FUSED ANOMALY",
            f"{fused_confidence:.1%}",
            fused_confidence,
            TOKENS["accent"],
            f"Alert reason: {alert_reason}",
        ),
    ]
    score_html = "".join(
        '<div class="soc-score">'
        '<div class="soc-score-top">'
        f'<span class="soc-score-label">{escape(label)}</span>'
        f'<span class="soc-score-value" style="color:{colour}">{escape(value)}</span></div>'
        f"{track(ratio, colour)}"
        f'<div class="soc-score-note">{escape(note)}</div></div>'
        for label, value, ratio, colour, note in scores
    )

    return (
        f'<div class="soc-verdict" style="border-left:3px solid {_tint(edge, 0.55)}">'
        f'<div class="soc-verdict-head">{family_chip(predicted_class)}{state}'
        '<div class="soc-verdict-headline">'
        '<div class="k">FUSED ANOMALY CONFIDENCE</div>'
        f'<div class="v">{fused_confidence:.1%}</div></div></div>'
        f'<div class="soc-scores">{score_html}</div></div>'
    )


def evidence_rows(drivers: Sequence[Mapping[str, Any]]) -> str:
    """Render SHAP drivers as signed contribution bars, largest magnitude first."""

    if not drivers:
        return '<div class="soc-card-sub">No SHAP drivers were generated for this verdict.</div>'

    magnitudes = [abs(float(d.get("shap_value", 0.0) or 0.0)) for d in drivers]
    peak = max(magnitudes) or 1.0

    parts = []
    for driver, magnitude in zip(drivers, magnitudes):
        direction = str(driver.get("direction", ""))
        supports = direction.startswith("supports")
        neutral = direction.startswith("is neutral")
        colour = (
            TOKENS["text-tertiary"]
            if neutral
            else (TOKENS["status-alert"] if supports else TOKENS["status-info"])
        )
        tag = "NEUTRAL" if neutral else ("SUPPORTS" if supports else "OPPOSES")
        value = driver.get("true_value")
        value_text = "—" if value is None else str(value)
        parts.append(
            '<div class="soc-evidence-row"><div class="soc-evidence-head">'
            f'<span class="soc-evidence-feature">{escape(str(driver.get("feature", "")))}</span>'
            f'<span class="soc-evidence-value">{escape(value_text)}</span>'
            f'<span class="soc-tag" style="background:{_tint(colour, 0.14)};color:{colour}">'
            f"{tag}</span></div>"
            f"{track(magnitude / peak, colour, opacity=0.85)}</div>"
        )
    return "".join(parts)


def protocol_card(
    name: str, dataset: str, accuracy: float, macro_f1: float, color: str, blurb: str
) -> str:
    return (
        '<div class="soc-proto"><div class="soc-proto-top"><div>'
        f'<div class="soc-proto-name">{escape(str(name))}</div>'
        f'<div class="soc-proto-set">{escape(str(dataset))}</div></div>'
        f'<div class="soc-proto-acc" style="color:{color}">{accuracy:.2%}'
        f'<div class="soc-proto-f1">macro F1 {macro_f1:.4f}</div></div></div>'
        f"{track(accuracy, color)}"
        f'<div class="soc-proto-blurb">{escape(str(blurb))}</div></div>'
    )


def empty_state(icon_svg: str, title: str, body: str) -> str:
    """Placeholder for a panel with nothing to show yet."""

    return (
        '<div class="soc-empty">'
        f'<div class="soc-empty-icon">{icon_svg}</div>'
        f'<div class="soc-empty-title">{escape(str(title))}</div>'
        f'<div class="soc-empty-body">{escape(str(body))}</div>'
        "</div>"
    )


ICON_SCAN = (
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<circle cx="11" cy="11" r="7" stroke="#6B7688" stroke-width="1.6"/>'
    '<path d="M16.5 16.5 21 21" stroke="#6B7688" stroke-width="1.6" stroke-linecap="round"/>'
    '<path d="M8.4 11.2l2 2 4-4.2" stroke="#6B7688" stroke-width="1.6" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

ICON_QUEUE = (
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<rect x="3" y="4" width="18" height="5" rx="1.6" stroke="#6B7688" stroke-width="1.6"/>'
    '<rect x="3" y="12" width="18" height="5" rx="1.6" stroke="#6B7688" stroke-width="1.6"/>'
    '<path d="M7 21h10" stroke="#6B7688" stroke-width="1.6" stroke-linecap="round"/></svg>'
)


def panel_header(title: str, subtitle: str = "") -> str:
    """Standalone section header.

    Use this when the panel body contains Streamlit widgets. Streamlit renders every
    ``st.html`` call as its own sanitized block, so an unclosed ``<div>`` cannot wrap
    later calls — the stray closing tag is stripped and the card collapses around the
    title. When the whole panel is static markup, use ``card()`` instead.
    """

    sub = f'<div class="soc-card-sub">{escape(str(subtitle))}</div>' if subtitle else ""
    return (
        '<div class="soc-panel-header">'
        f'<div class="soc-card-title">{escape(str(title))}</div>{sub}</div>'
    )


def card(title: str, subtitle: str = "", body: str = "") -> str:
    """A complete bordered card. `body` must already be safe HTML from this module."""

    sub = f'<div class="soc-card-sub">{escape(str(subtitle))}</div>' if subtitle else ""
    return (
        '<div class="soc-card">'
        f'<div class="soc-card-title">{escape(str(title))}</div>{sub}{body}</div>'
    )
