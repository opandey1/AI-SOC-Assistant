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


# Native pills expose sibling buttons in CORRECTABLE_CLASSES order (tested below).
_REVIEW_FAMILY_STYLES = "\n".join(
    f".st-key-review_corrected_class button:nth-child({index}) "
    f"{{ --soc-family-color: {color}; }}"
    for index, (family, color) in enumerate(
        ((family, color) for family, color in FAMILY_COLORS.items() if family != "unknown"),
        start=1,
    )
)


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
    overflow-wrap: anywhere;
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
    border-radius: 10px; padding: 12px 16px; min-height: 140px;
    box-sizing: border-box; display: flex; flex-direction: column; gap: 8px;
}}
.soc-proto + .soc-proto {{ margin-top: 14px; }}
.soc-proto-top {{
    display: flex; flex-wrap: wrap; justify-content: space-between;
    align-items: flex-start; gap: 8px 12px; min-height: 48px;
}}
.soc-proto-identity {{ flex: 1 1 230px; min-width: 0; }}
.soc-proto-name {{
    font-size: 14px; line-height: 20px; font-weight: 600; color: var(--soc-text-primary);
}}
.soc-proto-set {{
    font-family: var(--soc-mono); font-size: 13px; line-height: 20px;
    color: var(--soc-text-tertiary); overflow-wrap: anywhere;
}}
.soc-proto-acc {{
    margin-left: auto; flex: 0 0 160px; text-align: right; font-family: var(--soc-mono);
    font-size: 20px; line-height: 28px; font-weight: 500; letter-spacing: 0;
}}
.soc-proto-f1 {{
    font-family: var(--soc-mono); font-size: 13px; line-height: 20px;
    font-weight: 400; color: var(--soc-text-tertiary);
}}
.soc-proto-blurb {{
    font-size: 13px; line-height: 20px; color: var(--soc-text-secondary);
    overflow-wrap: anywhere;
}}
.soc-proto .soc-track, .soc-proto .soc-track > span {{ border-radius: 3.5px; }}

/* Native containers retain widget behaviour; keys scope the Figma panel layout. */
.block-container:has(.st-key-model_operations) {{ padding-left: 26px; padding-right: 26px; }}
.st-key-model_operations {{ letter-spacing: 0; }}
.st-key-model_operations h2 {{ font-size: 22px; line-height: 28px; padding: 0; }}
.st-key-model_operations [data-testid="stCaptionContainer"] {{
    font-size: 13px; line-height: 20px; color: var(--soc-text-secondary);
}}
.st-key-model_operations .soc-tile-row {{ gap: 12px; }}
.st-key-model_operations .soc-tile {{ min-height: 94px; box-sizing: border-box; }}
.st-key-model_operations .soc-tile-label,
.st-key-model_operations .soc-tile-value {{ letter-spacing: 0; }}
.st-key-model_split [data-testid="stHorizontalBlock"] {{ gap: 18px; }}
.st-key-feedback_retraining, .st-key-evaluation_protocols {{
    background: var(--soc-bg-surface); border: 1px solid var(--soc-border-subtle);
    border-radius: 12px; padding: 16px 18px 18px; min-height: 713px;
}}
.st-key-model_operations .soc-panel-header {{ margin: 0; }}
.st-key-model_operations .soc-card-title {{
    font-size: 15px; line-height: 21px; letter-spacing: 0;
}}
.st-key-model_operations .soc-card-sub {{
    font-size: 13px; line-height: 20px; color: var(--soc-text-secondary);
}}
.st-key-feedback_retraining .soc-callout {{ margin: 0; }}
.st-key-feedback_retraining .soc-callout .body {{ font-size: 12px; line-height: 18px; }}
.st-key-feedback_retraining [data-testid="stSlider"] label p {{
    font-size: 12px; color: var(--soc-text-secondary);
}}
.soc-cohort {{
    width: 100%; table-layout: fixed; border-collapse: separate; border-spacing: 0;
    border: 1px solid var(--soc-border-subtle); border-radius: 8px;
    overflow: hidden; font-family: var(--soc-mono); font-size: 11px; line-height: 18px;
}}
.soc-cohort th {{
    background: var(--soc-bg-elevated); color: var(--soc-text-tertiary);
    font-family: var(--soc-font); font-size: 10px; font-weight: 500; text-align: left;
}}
.soc-cohort th, .soc-cohort td {{ padding: 8px 10px; overflow-wrap: anywhere; }}
.soc-cohort th:first-child {{ width: 40px; }}
.soc-cohort th:nth-child(2) {{ width: 35%; }}
.soc-cohort td {{ background: var(--soc-bg-inset); color: var(--soc-text-secondary); }}
.soc-cohort tr + tr td {{ border-top: 1px solid var(--soc-border-subtle); }}

/* Review controls keep native input semantics and ticket-scoped widget state. */
.st-key-review_workspace {{ letter-spacing: 0; }}
.st-key-review_workspace h2 {{ font-size: 22px; line-height: 28px; padding: 0; }}
.st-key-review_workspace .soc-card-title,
.st-key-review_workspace .soc-tile-value,
.st-key-review_workspace .soc-tile-label {{ letter-spacing: 0; }}
.st-key-review_workspace .soc-card,
.st-key-review_workspace .soc-empty,
.st-key-review_workspace .soc-tile {{ border-radius: 8px; }}
.st-key-review_workspace .soc-kv-row {{ flex-wrap: wrap; }}
.st-key-review_workspace .soc-kv-v {{ min-width: 0; overflow-wrap: anywhere; }}
.st-key-review_detail .soc-kv {{ background: transparent; border: 0; padding: 0; }}
.st-key-review_workspace .soc-empty {{ min-height: 200px; box-sizing: border-box; }}
.st-key-review_disposition [role="radiogroup"] {{ gap: 8px; width: 100%; }}
.st-key-review_disposition label[data-testid="stRadioOption"] {{
    width: 100%; margin: 0; box-sizing: border-box; padding: 12px;
    border: 1px solid var(--soc-border-default); border-radius: 8px;
    background: var(--soc-bg-surface); min-height: 74px;
}}
.st-key-review_disposition label[data-testid="stRadioOption"]:hover {{
    border-color: var(--soc-border-strong);
}}
.st-key-review_disposition label[data-testid="stRadioOption"]:has(input:checked) {{
    border-color: var(--soc-accent); background: var(--soc-bg-elevated);
}}
.st-key-review_disposition label[data-testid="stRadioOption"]:has(input:focus-visible) {{
    outline: 2px solid var(--soc-accent-hover); outline-offset: 2px;
}}
.st-key-review_detail [data-testid="stForm"] {{
    padding: 0; border: 0; background: transparent;
}}
{_REVIEW_FAMILY_STYLES}
.st-key-review_corrected_class button {{
    min-height: 36px; letter-spacing: 0; color: var(--soc-family-color);
    border-color: color-mix(in srgb, var(--soc-family-color) 50%, var(--soc-border-default));
}}
.st-key-review_corrected_class [data-testid="stButtonGroup"] button[aria-checked="true"] {{
    color: var(--soc-family-color);
    background: color-mix(in srgb, var(--soc-family-color) 15%, var(--soc-bg-surface));
    border-color: var(--soc-family-color);
}}
.st-key-review_corrected_class button:focus-visible {{
    outline: 2px solid var(--soc-accent-hover); outline-offset: 2px;
}}
/* Triage keeps native controls and separates RF evidence from the fused verdict. */
.st-key-triage_workspace {{ letter-spacing: 0; }}
.st-key-triage_workspace h2 {{ font-size: 22px; line-height: 28px; padding: 0; }}
.st-key-triage_workspace .soc-verdict,
.st-key-triage_workspace .soc-card,
.st-key-triage_workspace .soc-empty,
.st-key-triage_controls [data-testid="stForm"] {{ border-radius: 8px; }}
.st-key-triage_workspace .soc-empty {{ min-height: 200px; box-sizing: border-box; }}
.st-key-triage_workspace .soc-verdict-head {{ flex-wrap: wrap; gap: 10px; }}
.st-key-triage_workspace .soc-scores {{
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px;
}}
.st-key-triage_workspace .soc-score {{ background: transparent; border: 0; padding: 0; }}
.st-key-triage_workspace .soc-score-label,
.st-key-triage_workspace .soc-score-value,
.st-key-triage_workspace .soc-verdict-headline .k,
.st-key-triage_workspace .soc-verdict-headline .v,
.st-key-triage_workspace .soc-card-title {{ letter-spacing: 0; }}
.st-key-triage_workspace .soc-evidence-head {{ flex-wrap: wrap; }}
.st-key-triage_workspace .soc-evidence-value {{ max-width: 100%; overflow-wrap: anywhere; }}
.st-key-triage_json textarea {{ font-family: var(--soc-mono); font-size: 12px; }}
@media (max-width: 1100px) {{
    .st-key-model_split [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
    .st-key-model_split [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
        width: 100%; flex: 1 1 100%;
    }}
    .st-key-feedback_retraining, .st-key-evaluation_protocols {{ min-height: 0; }}
    .st-key-review_workspace [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
    .st-key-review_workspace [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
        width: 100%; flex: 1 1 100%;
    }}
    .st-key-triage_workspace [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
    .st-key-triage_workspace [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
        width: 100%; flex: 1 1 100%;
    }}
    .st-key-triage_workspace .soc-scores {{ grid-template-columns: 1fr; }}
}}
@media (max-width: 480px) {{
    .block-container:has(.st-key-model_operations, .st-key-review_workspace, .st-key-triage_workspace) .soc-topbar {{
        flex-wrap: wrap; gap: 8px;
    }}
    .block-container:has(.st-key-model_operations, .st-key-review_workspace, .st-key-triage_workspace) .soc-brand {{
        width: 100%;
    }}
    .block-container:has(.st-key-model_operations, .st-key-review_workspace, .st-key-triage_workspace) .soc-brand-name {{
        white-space: nowrap;
    }}
    .block-container:has(.st-key-model_operations, .st-key-review_workspace, .st-key-triage_workspace) .soc-topbar-spacer {{
        display: none;
    }}
    .block-container:has(.st-key-model_operations, .st-key-review_workspace, .st-key-triage_workspace) .soc-runtime {{
        width: 100%; flex-wrap: wrap;
    }}
    .block-container:has(.st-key-review_workspace, .st-key-triage_workspace) .soc-topbar {{ padding-left: 36px; }}
    .st-key-triage_workspace .soc-verdict-headline {{ flex-basis: 100%; margin-left: 0; text-align: left; }}
    .st-key-review_workspace .soc-tile-row {{
        display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px;
    }}
    .st-key-feedback_retraining, .st-key-evaluation_protocols {{ padding: 14px; }}
    .soc-proto-identity {{ flex-basis: 100%; }}
    .soc-proto-acc {{ flex-basis: 100%; text-align: left; margin-left: 0; }}
    .soc-cohort th, .soc-cohort td {{ padding: 6px; }}
}}

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
    heading = (
        f'<div class="title" style="color:{color}">{escape(str(title))}</div>' if title else ""
    )
    return (
        f'<div class="soc-callout" style="background:{_tint(color, 0.10)};'
        f'border:1px solid {_tint(color, 0.30)}">'
        f'<div class="rule" style="background:{color}"></div><div>'
        f"{heading}"
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


def cohort_table(rows: Sequence[tuple[int, str, str, str]]) -> str:
    """Compact, read-only view of the latest reviewed false-positive tickets."""

    body = "".join(
        f"<tr><td>{escape('#' + str(ticket_id))}</td><td>{escape(str(event_id))}</td>"
        f'<td style="color:{family_color(predicted)}">{escape(str(predicted))}</td>'
        f'<td style="color:{family_color(corrected)}">{escape(str(corrected))}</td></tr>'
        for ticket_id, event_id, predicted, corrected in rows
    )
    return (
        '<table class="soc-cohort" aria-label="Reviewed false-positive cohort">'
        '<thead><tr><th scope="col">ID</th><th scope="col">EVENT</th>'
        '<th scope="col">PREDICTED</th><th scope="col">CORRECTED</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


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


def evidence_rows(
    drivers: Sequence[Mapping[str, Any]], *, predicted_class: str | None = None
) -> str:
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
            else (
                (family_color(predicted_class) if predicted_class else TOKENS["status-alert"])
                if supports
                else TOKENS["status-info"]
            )
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
        '<div class="soc-proto"><div class="soc-proto-top"><div class="soc-proto-identity">'
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
    '<span class="material-symbols-rounded" aria-hidden="true" '
    'style="font-size:26px;line-height:1;color:#6B7688">search_check</span>'
)

# Streamlit 1.60's HTML sanitiser strips inline SVG; reuse its loaded icon font.
ICON_QUEUE = (
    '<span class="material-symbols-rounded" aria-hidden="true" '
    'style="font-size:26px;line-height:1;color:#6B7688">view_list</span>'
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
