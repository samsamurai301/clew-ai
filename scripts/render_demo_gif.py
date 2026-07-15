"""Render a truthful 52-second GIF from the real offline demo output.

Run from the repository root with:

    uv run --with 'pillow>=11,<12' python scripts/render_demo_gif.py

Pillow is deliberately a release-asset dependency, not a Clew runtime dependency.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets" / "clew-demo.gif"
WIDTH, HEIGHT = 1200, 675
BACKGROUND = "#0d1117"
PANEL = "#161b22"
TEXT = "#e6edf3"
MUTED = "#8b949e"
GREEN = "#3fb950"
CYAN = "#58a6ff"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("/System/Library/Fonts/Menlo.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
        Path("C:/Windows/Fonts/consola.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _run_demo() -> list[str]:
    with tempfile.TemporaryDirectory(prefix="clew-gif-") as tmp:
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "clew", "demo"],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
    return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]


def _frame(command: str, output: list[str], *, cursor: bool) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = _font(26)
    mono = _font(25)
    small = _font(19)

    draw.rounded_rectangle((34, 28, WIDTH - 34, HEIGHT - 28), radius=16, fill=PANEL)
    draw.ellipse((58, 52, 76, 70), fill="#ff5f56")
    draw.ellipse((86, 52, 104, 70), fill="#ffbd2e")
    draw.ellipse((114, 52, 132, 70), fill="#27c93f")
    draw.text(
        (WIDTH // 2, 61), "Clew 1.1.5 — offline demo", font=title_font, fill=MUTED, anchor="mm"
    )

    y = 112
    draw.text((66, y), "$", font=mono, fill=GREEN)
    draw.text((98, y), command + ("▋" if cursor else ""), font=mono, fill=TEXT)
    y += 55
    for line in output:
        color = CYAN if line.startswith("Clew offline") else TEXT
        draw.text((66, y), line, font=mono, fill=color)
        y += 43

    draw.text(
        (66, HEIGHT - 68),
        "No API key · no server · no analytics · report stays local",
        font=small,
        fill=MUTED,
    )
    return image


def main() -> None:
    lines = _run_demo()
    command = "uvx clew-ai demo"
    typed = ["uvx", "uvx clew-ai", command]
    frames = [_frame(part, [], cursor=True) for part in typed]
    durations = [2_000, 2_000, 4_000]

    visible: list[str] = []
    for line in lines:
        visible.append(line)
        frames.append(_frame(command, visible, cursor=False))
        durations.append(6_000)

    frames.append(_frame(command, visible, cursor=True))
    durations.append(8_000)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    total_seconds = sum(durations) / 1_000
    print(f"wrote {OUT} ({len(frames)} frames, {total_seconds:.0f}s)")


if __name__ == "__main__":
    main()
