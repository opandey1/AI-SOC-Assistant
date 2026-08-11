"""Generate the README terminal demo and incident-ticket preview."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs"

BACKGROUND = "#0d1117"
TITLEBAR = "#161b22"
BODY = "#b8c0cc"
MUTED = "#8b949e"
COMMAND = "#e6edf3"
PROMPT = "#7ee787"
HEADING = "#79c0ff"
SUCCESS = "#7ee787"
WARNING = "#ffd166"
QUERY = "#56d4dd"


@dataclass(frozen=True)
class TerminalLine:
    text: str
    color: str = BODY
    indent: int = 0
    bold: bool = False


LINES = [
    TerminalLine("$ python -m src.pipeline --no-llm", COMMAND),
    TerminalLine("Loaded 125,973 training rows and 22,544 test rows.", MUTED),
    TerminalLine("Prepared 122 model features. Class balancing: random_oversampling.", MUTED),
    TerminalLine(""),
    TerminalLine("=== SOC Incident Ticket ===", SUCCESS, bold=True),
    TerminalLine(""),
    TerminalLine("1. Incident Summary", HEADING, bold=True),
    TerminalLine("Connection from 192.168.1.47 was flagged as suspicious.", BODY, indent=1),
    TerminalLine("RF: dos (100.0%) | IF risk: 82.6% | fused confidence: 93.0%", WARNING, indent=1),
    TerminalLine(""),
    TerminalLine("2. Attack Classification", HEADING, bold=True),
    TerminalLine("Type: dos | Escalation candidate: P2", BODY, indent=1),
    TerminalLine(""),
    TerminalLine("3. Why Flagged - Evidence", HEADING, bold=True),
    TerminalLine("- flag_S0 = 1.0; supports the predicted class.", BODY, indent=1),
    TerminalLine("- dst_host_srv_serror_rate = 1.0; supports the predicted class.", BODY, indent=1),
    TerminalLine("- Isolation Forest risk 82.6% exceeds threshold 70.0%.", BODY, indent=1),
    TerminalLine(""),
    TerminalLine("4. Immediate Containment", HEADING, bold=True),
    TerminalLine("Validate the asset; restrict it if activity is unauthorized.", BODY, indent=1),
    TerminalLine("5. Investigation", HEADING, bold=True),
    TerminalLine('index=network src_ip="192.168.1.47" earliest=-24h', QUERY, indent=1),
    TerminalLine("6. Escalation: P2; raise to P1 for confirmed impact.", HEADING, bold=True),
]


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    properties = font_manager.FontProperties(
        family="DejaVu Sans Mono",
        weight="bold" if bold else "normal",
    )
    return ImageFont.truetype(font_manager.findfont(properties), size)


def _terminal_frame(
    *,
    width: int,
    height: int,
    font_size: int,
    line_height: int,
    visible_lines: int,
    show_footer: bool,
) -> Image.Image:
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    regular = _font(font_size)
    bold = _font(font_size, bold=True)
    small = _font(max(12, font_size - 5))

    title_height = 46
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=12, fill=BACKGROUND)
    draw.rounded_rectangle((0, 0, width - 1, title_height), radius=12, fill=TITLEBAR)
    draw.rectangle((0, title_height - 12, width - 1, title_height), fill=TITLEBAR)
    for x, color in ((22, "#ff5f56"), (43, "#ffbd2e"), (64, "#27c93f")):
        draw.ellipse((x - 7, 16, x + 7, 30), fill=color)
    draw.text(
        (width // 2, 23),
        "AI-SOC-Assistant | offline pipeline demo",
        fill=MUTED,
        font=small,
        anchor="mm",
    )

    y = title_height + 18
    indent_width = regular.getlength("  ")
    for line in LINES[:visible_lines]:
        draw.text(
            (28 + line.indent * indent_width, y),
            line.text,
            fill=line.color,
            font=bold if line.bold else regular,
        )
        y += line_height

    if visible_lines < len(LINES):
        draw.rectangle((28, y + 3, 28 + font_size // 2, y + font_size + 3), fill=PROMPT)
    if show_footer:
        footer = "Validated detector evidence | deterministic template renderer | no network calls"
        draw.text((28, height - 26), footer, fill=MUTED, font=small)
    return image


def generate_ticket_preview() -> Path:
    output = DOCS_DIR / "ticket_preview.png"
    image = _terminal_frame(
        width=1400,
        height=900,
        font_size=23,
        line_height=32,
        visible_lines=len(LINES),
        show_footer=True,
    )
    image.save(output, format="PNG", optimize=True)
    return output


def generate_demo_gif() -> Path:
    output = DOCS_DIR / "demo.gif"
    frames = []
    for visible in range(1, len(LINES) + 1):
        frames.append(
            _terminal_frame(
                width=1000,
                height=690,
                font_size=16,
                line_height=25,
                visible_lines=visible,
                show_footer=visible == len(LINES),
            )
        )

    durations = [230] * (len(frames) - 1) + [2600]
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    return output


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for artifact in (generate_ticket_preview(), generate_demo_gif()):
        print(f"Generated {artifact.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
