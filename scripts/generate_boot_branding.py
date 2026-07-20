#!/usr/bin/env python3
"""Generate the fixed-size LabFoundry GRUB background from the product mark."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 640
HEIGHT = 480
SCALE = 2


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size * SCALE)
    return ImageFont.load_default()


def _centered(draw: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.ImageFont, fill: str) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((WIDTH * SCALE - (box[2] - box[0])) / 2, y * SCALE), text, font=font, fill=fill)


def generate(output: Path, photon_logo_path: Path) -> None:
    width = WIDTH * SCALE
    height = HEIGHT * SCALE
    image = Image.new("RGB", (width, height), "#0f172a")
    pixels = image.load()
    for y in range(height):
        progress = y / max(height - 1, 1)
        for x in range(width):
            glow = max(0.0, 1.0 - (((x - width / 2) / (width * 0.7)) ** 2 + ((y - height * 0.32) / (height * 0.8)) ** 2))
            pixels[x, y] = (
                int(15 + 17 * glow),
                int(23 + 49 * glow),
                int(42 + 80 * glow + 10 * progress),
            )

    draw = ImageDraw.Draw(image)
    mark_left = 272 * SCALE
    mark_top = 48 * SCALE
    mark_size = 96 * SCALE
    draw.rounded_rectangle(
        (mark_left, mark_top, mark_left + mark_size, mark_top + mark_size),
        radius=22 * SCALE,
        fill="#dbeafe",
        outline="#93c5fd",
        width=2 * SCALE,
    )
    for row in (78, 96, 114):
        draw.line((294 * SCALE, row * SCALE, 346 * SCALE, row * SCALE), fill="#1d4ed8", width=4 * SCALE)
    draw.line((320 * SCALE, 68 * SCALE, 320 * SCALE, 126 * SCALE), fill="#60a5fa", width=3 * SCALE)
    for x in (294, 346):
        for y in (78, 114):
            draw.ellipse(
                ((x - 6) * SCALE, (y - 6) * SCALE, (x + 6) * SCALE, (y + 6) * SCALE),
                fill="#ffffff",
                outline="#2563eb",
                width=3 * SCALE,
            )
    draw.polygon(
        ((320 * SCALE, 72 * SCALE), (307 * SCALE, 96 * SCALE), (312 * SCALE, 115 * SCALE), (320 * SCALE, 124 * SCALE),
         (328 * SCALE, 115 * SCALE), (333 * SCALE, 96 * SCALE)),
        fill="#f59e0b",
    )
    draw.ellipse((309 * SCALE, 91 * SCALE, 331 * SCALE, 121 * SCALE), fill="#0f766e", outline="#ffffff", width=3 * SCALE)

    _centered(draw, 168, "LabFoundry", _font(40, bold=True), "#f8fafc")
    draw.rounded_rectangle((154 * SCALE, 230 * SCALE, 486 * SCALE, 234 * SCALE), radius=2 * SCALE, fill="#2563eb")
    _centered(draw, 404, "Powered by", _font(13), "#bfdbfe")
    photon_logo = Image.open(photon_logo_path).convert("RGBA")
    # Ignore nearly transparent edge noise when trimming the supplied artwork.
    alpha_box = photon_logo.getchannel("A").point(lambda alpha: 255 if alpha >= 16 else 0).getbbox()
    if alpha_box:
        photon_logo = photon_logo.crop(alpha_box)
    max_logo_width = 210 * SCALE
    max_logo_height = 34 * SCALE
    logo_scale = min(max_logo_width / photon_logo.width, max_logo_height / photon_logo.height)
    target_width = max(1, round(photon_logo.width * logo_scale))
    target_height = max(1, round(photon_logo.height * logo_scale))
    photon_logo = photon_logo.resize((target_width, target_height), Image.Resampling.LANCZOS)
    logo_x = int((width - photon_logo.width) / 2)
    logo_y = int(427 * SCALE + (42 * SCALE - photon_logo.height) / 2)
    badge_padding = 14 * SCALE
    badge = (
        logo_x - badge_padding,
        426 * SCALE,
        logo_x + photon_logo.width + badge_padding,
        470 * SCALE,
    )
    draw.rounded_rectangle(badge, radius=12 * SCALE, fill="#f8fafc", outline="#bfdbfe", width=2 * SCALE)
    image.paste(photon_logo, (logo_x, logo_y), photon_logo)

    image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--photon-logo",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "image/common/boot/grub/photon-os-logo.png",
    )
    args = parser.parse_args()
    generate(args.output, args.photon_logo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
