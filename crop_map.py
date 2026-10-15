"""
crop_map.py - turn a cubiomes screenshot into a clean map.png

Cubiomes screenshots contain red square outline(s) drawn on top of the map.
This script finds those outlines automatically and crops the image just
inside of them, so the result has no red (or red-tinted anti-aliased) pixels.

Usage (command line):
    python crop_map.py screenshot.png              # -> map.png
    python crop_map.py screenshot.png out.png
    python crop_map.py screenshot.png --square inner   # cut inside the INNER square instead

Usage (from another project):
    from crop_map import crop_map
    crop_map("screenshot.png", "map.png")

--square outer (default): crop just inside the outermost red square (an inner
                          square, if present, stays visible in the result).
--square inner           : crop inside the innermost red square -> no red anywhere.
"""
import argparse
import sys

import numpy as np
from PIL import Image

MARGIN = 2          # extra pixels trimmed past the line to drop faint anti-aliased tint
MAX_LINE_WIDTH = 6  # a red outline is thin; wider red runs are just terrain (desert, etc.)
MIN_COVERAGE = 0.6  # fraction of a row/column that must look red to count as a line
NEIGHBOR = 3        # compare each pixel against pixels this far away across the line
MIN_RED_SHIFT = 25  # how much redder (r - g) than both neighbours a line pixel must be


def _redness_mask(rgb, axis):
    """True for pixels tinted red relative to the background on both sides of them.

    The overlay is semi-transparent, so absolute redness fails over bright green /
    cyan terrain. Instead compare r - g against the pixels NEIGHBOR px away across
    the line (left/right for vertical lines, above/below for horizontal ones).
    Biome edges don't trigger this because one side always matches the pixel.
    """
    rg = rgb[:, :, 0].astype(int) - rgb[:, :, 1].astype(int)
    pad = [(0, 0), (0, 0)]
    pad[axis] = (NEIGHBOR, NEIGHBOR)
    p = np.pad(rg, pad, mode="edge")
    n = rg.shape[axis]
    before = np.take(p, range(0, n), axis=axis)
    after = np.take(p, range(2 * NEIGHBOR, n + 2 * NEIGHBOR), axis=axis)
    return np.minimum(rg - before, rg - after) > MIN_RED_SHIFT


def _find_lines(coverage, min_cov=MIN_COVERAGE, max_width=MAX_LINE_WIDTH):
    """Return [(start, end), ...] index runs whose coverage is high and that are thin."""
    hits = coverage >= min_cov
    runs, i, n = [], 0, len(hits)
    while i < n:
        if hits[i]:
            j = i
            while j + 1 < n and hits[j + 1]:
                j += 1
            if j - i + 1 <= max_width:
                runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def detect_squares(img):
    """Return (xs, ys): lists of (start, end) pixel runs of vertical / horizontal red lines."""
    rgb = np.array(img.convert("RGB"))
    h, w = rgb.shape[:2]
    xs = _find_lines(_redness_mask(rgb, axis=1).sum(axis=0) / h)
    ys = _find_lines(_redness_mask(rgb, axis=0).sum(axis=1) / w)
    return xs, ys


def crop_map(input_path, output_path="map.png", square="outer", margin=MARGIN):
    img = Image.open(input_path)
    w, h = img.size
    xs, ys = detect_squares(img)
    if len(xs) < 2 or len(ys) < 2:
        raise RuntimeError(
            f"Could not find a red square (found {len(xs)} vertical / {len(ys)} horizontal lines). "
            "Is this a cubiomes screenshot with the red outline visible?")

    # Split lines into left/right and top/bottom groups around the image center.
    left = [r for r in xs if r[1] < w / 2]
    right = [r for r in xs if r[0] >= w / 2]
    top = [r for r in ys if r[1] < h / 2]
    bottom = [r for r in ys if r[0] >= h / 2]
    if not (left and right and top and bottom):
        raise RuntimeError("Red outline not found on all four sides.")

    if square == "outer":
        l = left[0][1] + 1 + margin
        r = right[-1][0] - margin
        t = top[0][1] + 1 + margin
        b = bottom[-1][0] - margin
    else:  # innermost square
        l = left[-1][1] + 1 + margin
        r = right[0][0] - margin
        t = top[-1][1] + 1 + margin
        b = bottom[0][0] - margin

    out = img.crop((l, t, r, b))
    out.save(output_path)
    print(f"Saved {output_path} ({out.width}x{out.height}) "
          f"cropped from box x={l}..{r}, y={t}..{b} of the {w}x{h} input")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Crop a cubiomes screenshot to a clean map.png")
    p.add_argument("input", help="screenshot PNG")
    p.add_argument("output", nargs="?", default="map.png", help="output file (default: map.png)")
    p.add_argument("--square", choices=["inner", "outer"], default="outer",
                   help="which red square to crop inside of (default: outer)")
    p.add_argument("--margin", type=int, default=MARGIN,
                   help="extra pixels to trim past the red line (default: %(default)s)")
    a = p.parse_args()
    try:
        crop_map(a.input, a.output, a.square, a.margin)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)