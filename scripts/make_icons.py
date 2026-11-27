"""Draw the app icons (a mic sticker on a saffron board) for the installable phone app."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "static"
SAFFRON, GREEN, INK, WHITE = (255, 154, 31), (11, 122, 75), (17, 17, 17), (255, 255, 255)


def icon(size: int) -> Image.Image:
    s = size / 512  # draw on a 512 grid, scale to the target size
    img = Image.new("RGB", (size, size), SAFFRON)
    d = ImageDraw.Draw(img)
    # green circle with a hard black offset shadow (neobrutalist sticker)
    d.ellipse([110 * s, 110 * s, 430 * s, 430 * s], fill=INK)
    d.ellipse([90 * s, 90 * s, 410 * s, 410 * s], fill=GREEN, outline=INK, width=int(12 * s))
    # microphone: capsule, cradle arc, stand, base
    d.rounded_rectangle([205 * s, 140 * s, 295 * s, 285 * s], radius=int(45 * s), fill=WHITE, outline=INK, width=int(8 * s))
    d.arc([170 * s, 200 * s, 330 * s, 330 * s], start=0, end=180, fill=WHITE, width=int(16 * s))
    d.line([250 * s, 330 * s, 250 * s, 362 * s], fill=WHITE, width=int(16 * s))
    d.rounded_rectangle([200 * s, 356 * s, 300 * s, 374 * s], radius=int(9 * s), fill=WHITE)
    return img


if __name__ == "__main__":
    for px in (192, 512):
        path = OUT / f"icon-{px}.png"
        icon(px).save(path, optimize=True)
        print(path.name, path.stat().st_size)
