"""Generate the two-page engineering evolution brief at docs/SOC_Assistant_Evolution.pdf.

The brief was previously produced ad hoc, so it drifted behind the repository. Keeping
the generator in version control makes the document reproducible: edit the CONTENT
blocks below and re-run.

    python scripts/generate_evolution_pdf.py

Requires reportlab, which is a documentation-only dependency and is deliberately not
part of the pinned runtime requirements.txt:

    python -m pip install reportlab
"""

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "SOC_Assistant_Evolution.pdf"

TITLE = "AI-SOC-Assistant Evolution &amp; Wins"
RUNNING_HEAD = "AI-SOC-Assistant - Engineering Evolution Brief"
UPDATED = "Updated 12 August 2026"

INK = HexColor("#1A1A1A")
MUTED = HexColor("#5F6672")
ACCENT = HexColor("#4B3FBE")
RULE = HexColor("#D5D8DE")
BAND = HexColor("#F2F3F7")

INTRO = (
    "A concise engineering brief showing how the project matured into a reproducible, "
    "explainable, and safety-conscious SOC triage pipeline with a live analyst workflow."
)

CAPABILITIES = [
    (
        "5-class family classification",
        "Separates Normal, DoS, Probe, R2L, and U2R instead of returning only a binary "
        "anomaly flag.",
    ),
    (
        "Stable dual-detector triage",
        "Combines supervised attack probability with a training-calibrated Isolation "
        "Forest risk signal.",
    ),
    (
        "Validated SHAP evidence",
        "Reports real feature values and whether each contribution supports or opposes "
        "the predicted class.",
    ),
    (
        "Governed ticket generation",
        "Defaults to a deterministic offline renderer and safely validates optional LLM " "output.",
    ),
    (
        "Closed analyst feedback loop",
        "Append-only SQLite reviews promote confirmed false positives into weighted "
        "Random Forest retraining.",
    ),
    (
        "External generalisation evidence",
        "A zero-tuning UNSW-NB15 transfer benchmark reports the cross-dataset gap "
        "rather than hiding it.",
    ),
]

SECTION_ONE = "Engineering Evolution"
ITEMS_ONE = [
    (
        "1. Robust Feature Encoding and Class Balance",
        "Categorical protocol, service, and flag fields use "
        'OneHotEncoder(handle_unknown="ignore"). Class imbalance is handled only on the '
        "training fold through deterministic duplication of exact rows, so categorical, "
        "binary, and integer telemetry is never interpolated.",
        "Novel categories no longer crash inference, and balanced training data remains "
        "valid network telemetry.",
    ),
    (
        "2. Stable Anomaly Calibration",
        "Isolation Forest calibration is learned once from training-normal traffic and "
        "reused for batch and single-record scoring. The fused score uses the probability "
        "of any non-normal class rather than the classifier's maximum confidence.",
        "Alert selection and ticket generation now agree, independent of unrelated "
        "test-batch rows.",
    ),
    (
        "3. LangGraph Orchestration with Pinned Compatibility",
        "The ReAct agent uses the state_modifier prompt API supported by the pinned "
        "LangGraph 0.2.53 stack, with direct provider integrations for Ollama, OpenAI, "
        "and Anthropic.",
        "Every supported provider can construct the agent graph without a "
        "version-signature failure.",
    ),
    (
        "4. Local-First Network Boundary",
        "Template mode makes no network calls, and the default Compose service runs with "
        "networking disabled. Ollama keeps prompts on the configured local server; cloud "
        "providers are explicitly external. AbuseIPDB and NVD tools remain off unless "
        "SOC_ENABLE_THREAT_INTEL=true is deliberately set.",
        "Privacy claims now match the actual deployment boundary and require explicit "
        "opt-in for external lookups.",
    ),
    (
        "5. Live Ingestion and a Closed Feedback Loop",
        "Delayed NSL-KDD replay and a Kafka-compatible consumer and publisher feed the "
        "same scoring runtime as the CLI. Generated tickets persist to SQLite, analyst "
        "reviews are append-only, and confirmed false positives re-enter training with an "
        "explicit per-row sample weight behind a versioned, atomically written artifact.",
        "The project demonstrates an operating loop rather than a batch script, and model "
        "promotion stays deliberate and auditable.",
    ),
]

SECTION_TWO = "Safety, Explainability, and Evaluation"
ITEMS_TWO = [
    (
        "6. Threat-Intelligence Correctness and Input Safety",
        "IP addresses and service names are validated before use. API calls have timeouts "
        "and malformed-response handling. NVD searches use a bounded 119-day publication "
        "window and sort results newest first.",
        "Unsafe inputs are rejected, and API failures cannot prevent deterministic "
        "incident-ticket generation.",
    ),
    (
        "7. Class-Aware SHAP Semantics",
        "Predicted labels are mapped through the fitted model's classes_ ordering, feature "
        "schemas must match exactly, and contribution directions are described as "
        "supporting, opposing, or neutral for the predicted class.",
        "Explanations remain correct for non-contiguous labels and avoid unsupported "
        "causal attack claims.",
    ),
    (
        "8. Evidence-Bound GenAI Guardrails",
        "Only allow-listed detector evidence reaches the LLM. Returned tickets must "
        "preserve validated detector semantics and evidence, use safe source-bound SPL "
        "queries, and omit raw SHAP values. Any malformed, unsafe, or provider-failed "
        "response falls back to the deterministic renderer.",
        "The optional LLM acts as a governed presentation layer rather than an authority "
        "over incident facts.",
    ),
    (
        "9. Protocol-Scoped, Reproducible Evaluation",
        "Hold-out, KDDTest+ cross-distribution, and external UNSW-NB15 transfer "
        "evaluations write separate artifacts. Current results are 99.88% hold-out "
        "accuracy, 74.40% cross-distribution accuracy, and 58.89% zero-tuning transfer "
        "accuracy at 16.02% macro F1. Dataset checksums, supported Python versions, and a "
        "shared-code notebook make reruns auditable.",
        "Strong in-distribution performance is presented alongside honest rare-class, "
        "distribution-shift, and cross-dataset limits.",
    ),
    (
        "10. Explanation-First Analyst Interface",
        "The Streamlit console is built on a single design-system module that defines the "
        "colour tokens, type ramp, and render helpers. SHAP drivers appear as signed "
        "contribution bars labelled as supporting or opposing the predicted class, while "
        "raw SHAP floats stay out of the ticket. Every rendered value is HTML-escaped "
        "because source IPs, event ids, and feature names reach the DOM.",
        "The explanation chain is legible to an analyst in one screen, and the interface "
        "inherits the same evidence-handling discipline as the pipeline.",
    ),
]

VALIDATION = (
    "Clean Python 3.10 to 3.12 dependency installs, 118 automated tests, formatting, "
    "lint, dependency consistency, default and no-balancing pipeline runs, and all three "
    "evaluation protocols pass. The analyst console is additionally verified by rendering "
    "it in a headless browser and asserting that no view raises, that Material icons "
    "resolve to glyphs, and that no element overflows, clips, or overlaps. Container "
    "build and non-root checks are enforced in CI across Python 3.10-3.12."
)

POSITIONING = (
    "The repository now demonstrates not only model training, but also production-minded "
    "evidence handling, network boundaries, dependency hygiene, regression coverage, a "
    "closed analyst feedback loop, and honest evaluation across three protocols. Those "
    "qualities make the project easier to operate, review, and extend in its next "
    "implementation phase."
)


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "base",
        fontName="Helvetica",
        fontSize=8.6,
        leading=12.2,
        textColor=INK,
        alignment=TA_LEFT,
    )
    return {
        "title": ParagraphStyle(
            "title", parent=base, fontName="Helvetica-Bold", fontSize=17, leading=21
        ),
        "intro": ParagraphStyle("intro", parent=base, fontSize=9.2, leading=13, textColor=MUTED),
        "section": ParagraphStyle(
            "section",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            spaceBefore=6,
            spaceAfter=3,
            textColor=ACCENT,
        ),
        "item": ParagraphStyle(
            "item", parent=base, fontName="Helvetica-Bold", fontSize=9.4, leading=12.6
        ),
        "body": base,
        "win": ParagraphStyle("win", parent=base, textColor=ACCENT),
        "cellhead": ParagraphStyle(
            "cellhead", parent=base, fontName="Helvetica-Bold", fontSize=8.4, leading=11
        ),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.4, leading=11),
        "foot": ParagraphStyle("foot", parent=base, fontSize=7.8, leading=10, textColor=MUTED),
    }


def _decorate(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, A4[1] - 12 * mm, RUNNING_HEAD)
    canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, A4[1] - 14 * mm, A4[0] - 18 * mm, A4[1] - 14 * mm)
    canvas.restoreState()


def _items(story, entries, s) -> None:
    for heading, detail, win in entries:
        story.append(Paragraph(heading, s["item"]))
        story.append(Paragraph(detail, s["body"]))
        story.append(Paragraph(f"<b>Win:</b> {win}", s["win"]))
        story.append(Spacer(1, 5))


def build(output: Path) -> Path:
    s = _styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=19 * mm,
        bottomMargin=15 * mm,
        title="AI-SOC-Assistant Engineering Evolution Brief",
        author="opandey1",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_decorate)])

    story = [Paragraph(TITLE, s["title"]), Spacer(1, 3), Paragraph(INTRO, s["intro"]), Spacer(1, 8)]

    rows = [
        [Paragraph("Capability", s["cellhead"]), Paragraph("Professional signal", s["cellhead"])]
    ]
    rows += [[Paragraph(a, s["cellhead"]), Paragraph(b, s["cell"])] for a, b in CAPABILITIES]
    table = Table(rows, colWidths=[52 * mm, 122 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BAND),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
                ("BOX", (0, 0), (-1, -1), 0.4, RULE),
            ]
        )
    )
    story += [table, Spacer(1, 9), Paragraph(SECTION_ONE, s["section"])]
    _items(story, ITEMS_ONE, s)

    story.append(PageBreak())
    story.append(Paragraph(SECTION_TWO, s["section"]))
    _items(story, ITEMS_TWO, s)
    story += [
        Spacer(1, 3),
        Paragraph("Validation snapshot", s["item"]),
        Paragraph(VALIDATION, s["body"]),
        Spacer(1, 7),
        Paragraph("Portfolio Positioning", s["item"]),
        Paragraph(POSITIONING, s["body"]),
        Spacer(1, 9),
        Paragraph(UPDATED, s["foot"]),
    ]

    doc.build(story)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    written = build(args.output)
    print(f"Wrote {written}")


if __name__ == "__main__":
    main()
