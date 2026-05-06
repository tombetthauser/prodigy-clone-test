"""
Trim sprite images: treat white touching the image border as background, make it
transparent, crop to the character, and save as PNG.

Assumes the margin around the character is white (or near-white). White pixels
that are not connected to the border (e.g. a white shirt) are kept opaque.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def _near_white(r: int, g: int, b: int, tolerance: int) -> bool:
    lo = 255 - tolerance
    return r >= lo and g >= lo and b >= lo


def trim_sprite_white_margin_to_png(
    src_path: str | Path,
    *,
    dst_path: str | Path | None = None,
    white_tolerance: int = 14,
) -> Path:
    """
    Load a sprite from disk, remove edge-connected near-white background (transparent),
    crop to non-transparent bounds, and write a PNG.

    :param src_path: Input image (PNG, JPEG, GIF, WebP, etc.).
    :param dst_path: Output PNG path. If None, overwrites ``src_path`` when it
        already ends in .png; otherwise writes ``src_path`` with suffix replaced
        by .png.
    :param white_tolerance: 0–255; RGB channels within this of 255 count as white.
    :returns: Path to the written PNG file.
    :raises ValueError: If nothing remains after trimming (empty sprite).
    :raises OSError, UnidentifiedImageError: If the file cannot be read as an image.
    """
    src = Path(src_path)
    if not src.is_file():
        raise OSError(f"not a file: {src}")

    if dst_path is None:
        if src.suffix.lower() == ".png":
            out = src
        else:
            out = src.with_suffix(".png")
    else:
        out = Path(dst_path)

    with Image.open(src) as im:
        im.load()
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        rgb = im.convert("RGB")
        w, h = rgb.size
        px = rgb.load()

        is_white = bytearray(w * h)
        for y in range(h):
            row = y * w
            for x in range(w):
                r, g, b = px[x, y]
                if _near_white(r, g, b, white_tolerance):
                    is_white[row + x] = 1

        bg = bytearray(w * h)
        q: deque[tuple[int, int]] = deque()
        for x in range(w):
            for y in (0, h - 1):
                i = y * w + x
                if is_white[i] and not bg[i]:
                    bg[i] = 1
                    q.append((x, y))
        for y in range(h):
            for x in (0, w - 1):
                i = y * w + x
                if is_white[i] and not bg[i]:
                    bg[i] = 1
                    q.append((x, y))

        while q:
            x, y = q.popleft()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                ni = ny * w + nx
                if is_white[ni] and not bg[ni]:
                    bg[ni] = 1
                    q.append((nx, ny))

        rgba = Image.new("RGBA", (w, h))
        opx = rgba.load()
        for y in range(h):
            row = y * w
            for x in range(w):
                i = row + x
                if bg[i]:
                    opx[x, y] = (0, 0, 0, 0)
                else:
                    r, g, b = px[x, y]
                    opx[x, y] = (r, g, b, 255)

        alpha = rgba.split()[-1]
        bbox = alpha.getbbox()
        if bbox is None:
            raise ValueError("sprite has no opaque pixels after removing white margin")

        cropped = rgba.crop(bbox)
        out.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(out, format="PNG", optimize=True)

    return out


def trim_sprite_empty_space_to_png(
    src_path: str | Path,
    *,
    dst_path: str | Path | None = None,
    alpha_threshold: int = 2,
) -> Path:
    """
    Crop away transparent/empty outer space on all sides and write PNG.

    This treats pixels with alpha <= alpha_threshold as empty and crops to the
    remaining opaque/semi-opaque bounds.
    """
    src = Path(src_path)
    if not src.is_file():
        raise OSError(f"not a file: {src}")

    if dst_path is None:
        out = src if src.suffix.lower() == ".png" else src.with_suffix(".png")
    else:
        out = Path(dst_path)

    with Image.open(src) as im:
        im.load()
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        rgba = im.convert("RGBA")
        a = rgba.split()[-1]
        if alpha_threshold > 0:
            a = a.point(lambda v: 0 if v <= alpha_threshold else 255)
        bbox = a.getbbox()
        if bbox is None:
            raise ValueError("sprite has no non-empty pixels")
        cropped = rgba.crop(bbox)
        out.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(out, format="PNG", optimize=True)
    return out
