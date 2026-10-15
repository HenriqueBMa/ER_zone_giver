#!/usr/bin/env python3
"""
Minecraft War Spawn Point Plotter
----------------------------------
Two modes:

1. GRID MODE  — overlay all valid spawn points on the map image.
   python mc_spawn_plotter.py --image map.png --size 4096 --spacing 300
   python mc_spawn_plotter.py --image map.png --size 1200 --spacing 300 --optimize-grid

2. WAR MODE   — read team picks, resolve conflicts, generate team image + commands.
   python mc_spawn_plotter.py --image map.png --size 4096 --spacing 300 --teams teams.txt

Teams file format (one team per line, color is REQUIRED, second token):
   TeamName  <color>  A1  C2  C3  D6  B2
   TEF       red      A1  C3  G2  F1
   Nomads    dark_blue

Zones file format (custom spawn points, one per line):
   500  -300
   -100 700
   # comment lines are ignored

Valid Minecraft 1.21 team colors:
  black, dark_blue, dark_green, dark_aqua, dark_red, dark_purple,
  gold, gray, dark_gray, blue, green, aqua, red, light_purple, yellow, white

Arguments:
    --image       Path to the Minecraft map image
    --size        Map side length in blocks (map goes from -size/2 to +size/2)
    --spacing     Distance D between spawn points in blocks
    --border      Minimum distance from border (default: 100 blocks)
    --output      Output image path (default: map_with_spawns.png)
    --teams       Path to teams text file (activates war mode)
    --war-out     Output image for war results (default: map_war_result.png)
    --dot-size    Radius of the small center dot in pixels (default: auto)
    --font-size   Font size in pixels (default: 30)
    --color       Grid dot color as R,G,B (default: 255,50,50)
    --grace_range Grace zone radius in blocks (default: 145)
    --seed        Random seed for conflict resolution (default: random)
    --exclude     Path to file with grid labels to exclude (default: exclude.txt)
    --zones       Path to file with custom x z coordinates (default: zones.txt)
"""

import argparse
import math
import random
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow is required. Install it with:  pip install Pillow")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Automatic water/beach zone exclusion
# ---------------------------------------------------------------------------

# Add more RGB colors here as needed.
WATER_BEACH_COLORS = {
    (32, 32, 112),
    (0,0,64),
    (0,0,112),
    (0,0,144),
    (0,0,172),
    (0, 0, 255),
    (112,112,214)
}

WATER_BEACH_THRESHOLD = 0.7

# ---------------------------------------------------------------------------
# Minecraft 1.21 team colors → RGB
# ---------------------------------------------------------------------------

MC_COLORS: dict[str, tuple[int, int, int]] = {
    "black":        (  0,   0,   0),
    "dark_blue":    (  0,   0, 170),
    "dark_green":   (  0, 170,   0),
    "dark_aqua":    (  0, 170, 170),
    "dark_red":     (170,   0,   0),
    "dark_purple":  (170,   0, 170),
    "gold":         (255, 170,   0),
    "gray":         (170, 170, 170),
    "dark_gray":    ( 85,  85,  85),
    "blue":         ( 85,  85, 255),
    "green":        ( 85, 255,  85),
    "aqua":         ( 85, 255, 255),
    "red":          (255,  85,  85),
    "light_purple": (255,  85, 255),
    "yellow":       (255, 255,  85),
    "white":        (255, 255, 255),
}


def _visible(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Brighten very dark colors so they're readable on the map."""
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum < 60:
        factor = 0.55
        r = int(r + (220 - r) * factor)
        g = int(g + (220 - g) * factor)
        b = int(b + (220 - b) * factor)
    return (r, g, b)

VISIBLE_MC_COLORS = {k: _visible(v) for k, v in MC_COLORS.items()}


# ---------------------------------------------------------------------------
# Grid label helpers  (0 → A, 1 → B, …, 25 → Z, 26 → AA, …)
# ---------------------------------------------------------------------------

def col_label(n: int) -> str:
    s = ""
    n += 1
    while n > 0:
        n -= 1
        s = chr(65 + n % 26) + s
        n //= 26
    return s

def make_label(col_idx: int, row_idx: int) -> str:
    return f"{col_label(col_idx)}{row_idx + 1}"


# ---------------------------------------------------------------------------
# Point generation
# ---------------------------------------------------------------------------

def find_optimal_grid_offset(
        map_size: int, spacing: int, border_margin: int
) -> tuple[int, int]:
    """
    Find the grid offset that fits the maximum number of points.

    The grid is periodic with period ``spacing``, so only offsets in
    [0, spacing) need to be considered. If several offsets fit the same
    maximum number of points, prefer the one whose resulting coordinates
    are most centered around 0.

    Returns (offset_x, offset_z).
    """
    if spacing <= 0:
        raise ValueError("spacing must be greater than 0")

    half = map_size / 2
    safe_min = -half + border_margin
    safe_max = half - border_margin

    def best_axis_offset() -> int:
        best_offset = 0
        best_count = -1
        best_center_distance = float("inf")

        for offset in range(spacing):
            first = math.ceil((safe_min - offset) / spacing)
            last = math.floor((safe_max - offset) / spacing)
            count = max(0, last - first + 1)

            if count == 0:
                center_distance = float("inf")
            else:
                first_pos = offset + first * spacing
                last_pos = offset + last * spacing
                center_distance = abs(first_pos + last_pos)

            if (
                count > best_count
                or (count == best_count and center_distance < best_center_distance)
            ):
                best_count = count
                best_offset = offset
                best_center_distance = center_distance

        return best_offset

    offset = best_axis_offset()
    return offset, offset


def generate_spawn_points(
        map_size: int, spacing: int, border_margin: int,
        optimize_grid: bool = False
) -> list[tuple[int, int, str]]:
    """
    Returns list of (block_x, block_z, grid_label) for all valid grid points.

    By default the grid remains centered on (0, 0), preserving the old
    behavior. With optimize_grid=True, the grid is shifted so that the
    maximum possible number of equally-spaced points fits inside the safe
    map area.
    """
    half = map_size / 2
    safe_min = -half + border_margin
    safe_max = half - border_margin

    if optimize_grid:
        offset_x, offset_z = find_optimal_grid_offset(
            map_size, spacing, border_margin
        )
        x_values = range(
            math.ceil((safe_min - offset_x) / spacing),
            math.floor((safe_max - offset_x) / spacing) + 1,
        )
        z_values = range(
            math.ceil((safe_min - offset_z) / spacing),
            math.floor((safe_max - offset_z) / spacing) + 1,
        )
        xs = [offset_x + k * spacing for k in x_values]
        zs = [offset_z + k * spacing for k in z_values]
        raw = [(int(bx), int(bz)) for bx in xs for bz in zs]
    else:
        # Original behavior: grid is centered on (0, 0).
        raw = []
        x = 0
        while x <= safe_max:
            xs = [x] if x == 0 else [x, -x]
            for bx in xs:
                if safe_min <= bx <= safe_max:
                    y = 0
                    while y <= safe_max:
                        ys = [y] if y == 0 else [y, -y]
                        for by in ys:
                            if safe_min <= by <= safe_max:
                                raw.append((int(bx), int(by)))
                        y += spacing
            x += spacing

    raw = sorted(set(raw))
    xs_sorted = sorted(set(p[0] for p in raw))
    zs_sorted = sorted(set(p[1] for p in raw))
    col_map = {v: i for i, v in enumerate(xs_sorted)}
    row_map = {v: i for i, v in enumerate(zs_sorted)}

    return [(bx, bz, make_label(col_map[bx], row_map[bz])) for bx, bz in raw]


# ---------------------------------------------------------------------------
# Custom zones loader  (labels: X1, X2, X3, …)
# ---------------------------------------------------------------------------

def parse_custom_zones(zones_path: str) -> list[tuple[int, int, str]]:
    """
    Read a file of custom spawn coordinates, one 'x z' pair per line.
    Returns [(bx, bz, 'X1'), (bx, bz, 'X2'), ...]
    """
    zones = []
    with open(zones_path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                print(f"  ⚠  zones line {lineno}: expected 'x z', got '{line}' — skipped")
                continue
            try:
                bx, bz = int(parts[0]), int(parts[1])
            except ValueError:
                print(f"  ⚠  zones line {lineno}: could not parse integers from '{line}' — skipped")
                continue
            zones.append((bx, bz, f"X{len(zones) + 1}"))
    return zones


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------

def blocks_to_pixels(bx: int, bz: int, map_size: int, img_w: int, img_h: int) -> tuple[int, int]:
    px = int((bx + map_size / 2) / map_size * img_w)
    py = int((bz + map_size / 2) / map_size * img_h)
    return px, py

def find_water_beach_zones(
        image: Image.Image,
        points: list[tuple[int, int, str]],
        map_size: int,
        grace_range: int,
) -> set[str]:
    """
    Find grid zones whose circular area is >= 80% water/beach.

    The circle is exactly the same size as the grace circle drawn on the map.
    A pixel is considered water/beach if its RGB value is in
    WATER_BEACH_COLORS.

    Returns a set of grid labels to automatically exclude.
    """
    img = image.convert("RGB")
    img_w, img_h = img.size
    pixels = img.load()

    grace_px = int(grace_range / map_size * img_w)
    radius_sq = grace_px * grace_px

    excluded = set()

    for bx, bz, label in points:
        px, py = blocks_to_pixels(
            bx, bz, map_size, img_w, img_h
        )

        total_pixels = 0
        water_beach_pixels = 0

        # Only inspect pixels inside the circular zone.
        min_x = max(0, px - grace_px)
        max_x = min(img_w - 1, px + grace_px)
        min_y = max(0, py - grace_px)
        max_y = min(img_h - 1, py + grace_px)

        for y in range(min_y, max_y + 1):
            dy = y - py

            for x in range(min_x, max_x + 1):
                dx = x - px

                if dx * dx + dy * dy > radius_sq:
                    continue

                total_pixels += 1

                if pixels[x, y] in WATER_BEACH_COLORS:
                    water_beach_pixels += 1

        if total_pixels == 0:
            continue

        percentage = water_beach_pixels / total_pixels

        if percentage >= WATER_BEACH_THRESHOLD:
            excluded.add(label)
            print(
                f"  Auto-excluded {label}: "
                f"{percentage:.1%} water/beach"
            )

    return excluded

# ---------------------------------------------------------------------------
# Text with drop-shadow
# ---------------------------------------------------------------------------

def draw_text_shadowed(draw, pos, text, font, fill, anchor="mm"):
    x, y = pos
    shadow = (0, 0, 0, 220)
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, 1), (0, -1)):
        draw.text((x + dx, y + dy), text, font=font, fill=shadow, anchor=anchor)
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


# ---------------------------------------------------------------------------
# Font loader
# ---------------------------------------------------------------------------

def load_fonts(font_size: int):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ]:
        try:
            label_font  = ImageFont.truetype(path, font_size)
            coords_font = ImageFont.truetype(path, max(9, int(font_size * 0.75)))
            small_font  = ImageFont.truetype(path, max(8, int(font_size * 0.65)))
            return label_font, coords_font, small_font
        except Exception:
            pass
    default = ImageFont.load_default()
    return default, default, default


# ---------------------------------------------------------------------------
# Team picks parser
# ---------------------------------------------------------------------------

def parse_teams(teams_path: str) -> list[tuple[str, str, list[str]]]:
    """
    Returns [(team_name, mc_color_key, [pick1, pick2, ...]), ...]
    Format: TeamName  <mc_color>  [pick1 pick2 ...]
    Color is required. Picks are optional (empty = fully random).
    """
    teams = []
    errors = []
    with open(teams_path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = "".join([c for c in line if c != ','])
            parts = line.split()

            if len(parts) < 2:
                errors.append(f"  Line {lineno}: '{line}' — missing color (format: Name color [picks...])")
                continue

            name = parts[0]
            color_key = parts[1].lower()

            if color_key not in MC_COLORS:
                valid = ", ".join(MC_COLORS.keys())
                errors.append(
                    f"  Line {lineno}: '{color_key}' is not a valid Minecraft color.\n"
                    f"    Valid colors: {valid}"
                )
                continue

            picks = [p.upper() for p in parts[2:]]
            teams.append((name, color_key, picks))

    if errors:
        print("── Teams file errors ────────────────────────────────")
        for e in errors:
            print(e)
        if not teams:
            sys.exit(1)

    teams = sorted(teams, key=lambda t: (t[1], t[2], t[0]))

    return teams


# ---------------------------------------------------------------------------
# Conflict resolver
# ---------------------------------------------------------------------------

def resolve_spawns(
        teams: list[tuple[str, str, list[str]]],
        label_to_point: dict[str, tuple[int, int]],
        excluded: set = None,
) -> tuple[dict[str, tuple[int, int, str, str]], list[str]]:
    """
    Priority-based conflict resolution with random tiebreaking.
    Returns assignments: {team_name: (bx, bz, label, mc_color_key)}
    """

    excluded = excluded or set()
    assigned: dict[str, tuple[int, int, str, str]] = {}
    taken: set[str] = set()
    log: list[str] = []
    waiting: list[tuple[str, str]] = []

    pending: dict[str, tuple[str, list[str]]] = {}
    for name, color_key, picks in teams:
        clean = [p for p in picks if p not in excluded]
        if len(clean) < len(picks):
            dropped = [p for p in picks if p in excluded]
            log.append(f"  ⚠  {name}: excluded picks removed: {', '.join(dropped)}")
        pending[name] = (color_key, clean)

    round_num = 0
    while pending:
        round_num += 1
        wants: dict[str, list[str]] = {}

        for team, (color_key, picks) in list(pending.items()):
            while picks and picks[0] in taken:
                picks.pop(0)
            if not picks:
                waiting.append((team, color_key))
                del pending[team]
                log.append(f"Round {round_num} | {team}: picks exhausted → moved to random waiting pool")
                continue
            spot = picks[0]
            if spot not in label_to_point:
                log.append(f"  ⚠  {team}: pick '{spot}' is not a valid grid point — skipped")
                picks.pop(0)
                continue
            wants.setdefault(spot, []).append(team)

        for spot, claimants in wants.items():
            if spot in taken:
                continue
            winner = random.choice(claimants)
            bx, bz = label_to_point[spot]
            color_key = pending[winner][0]
            assigned[winner] = (bx, bz, spot, color_key)
            taken.add(spot)
            del pending[winner]

            losers = [c for c in claimants if c != winner]
            if losers:
                log.append(
                    f"Round {round_num} | {spot}: conflict between "
                    f"{', '.join(claimants)} → 🎲 {winner} wins"
                )
                for loser in losers:
                    pending[loser][1].pop(0)
                    log.append(f"  ↳ {loser} moves to next pick")
            else:
                log.append(f"Round {round_num} | {spot}: assigned to {winner}")

    if waiting:
        log.append(f"--- Random fallback phase: {', '.join(t for t, _ in waiting)} ---")
        random.shuffle(waiting)
        for team, color_key in waiting:
            free = [lbl for lbl in label_to_point if lbl not in taken]
            if not free:
                log.append(f"  ✗  {team}: no free spots left!")
                continue
            spot = random.choice(free)
            bx, bz = label_to_point[spot]
            assigned[team] = (bx, bz, spot, color_key)
            taken.add(spot)
            log.append(f"  ✗  {team}: random fallback → {spot} ({bx}, {bz})")

    return assigned, log


# ---------------------------------------------------------------------------
# Shared point drawing helper
# ---------------------------------------------------------------------------

def draw_point(draw, px, py, dot_radius, grace_px, fill_color, label, coords_str,
               label_font, coords_font, white_solid=(255, 255, 255, 255)):
    """Draw one spawn point: grace circle + dot + label above + coords below."""
    draw.ellipse(
        [px - grace_px, py - grace_px, px + grace_px, py + grace_px],
        outline=(*fill_color[:3], 120),
        width=max(1, dot_radius // 2),
    )
    draw.ellipse(
        [px - dot_radius - 1, py - dot_radius - 1,
         px + dot_radius + 1, py + dot_radius + 1],
        fill=white_solid,
    )
    draw.ellipse(
        [px - dot_radius, py - dot_radius, px + dot_radius, py + dot_radius],
        fill=fill_color,
    )
    gap = dot_radius + 3
    draw_text_shadowed(draw, (px, py - gap), label,      label_font, fill_color,            anchor="mb")
    draw_text_shadowed(draw, (px, py + gap), coords_str, coords_font, (200, 200, 200, 220), anchor="mt")


# ---------------------------------------------------------------------------
# Grid image (mode 1)
# ---------------------------------------------------------------------------

def plot_grid(
        image_path: str,
        map_size: int,
        spacing: int,
        border_margin: int = 100,
        output_path: str = "map_with_spawns.png",
        dot_radius: int | None = None,
        color: tuple = (255, 50, 50),
        font_size: int | None = None,
        grace_range: int = 145,
        excluded: set = None,
        custom_zones: list = None,
        optimize_grid: bool = False,
):
    img = Image.open(image_path).convert("RGBA")
    img_w, img_h = img.size

    if dot_radius is None:
        dot_radius = max(3, int((img_w + img_h) / 2 * 0.0035))
    if font_size is None:
        font_size = max(11, int((img_w + img_h) / 2 * 0.013))

    label_font, coords_font, _ = load_fonts(font_size)

    points = generate_spawn_points(
        map_size, spacing, border_margin, optimize_grid=optimize_grid
    )
    if excluded:
        points = [(bx, bz, lbl) for bx, bz, lbl in points if lbl not in excluded]
    if custom_zones:
        points = points + custom_zones

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    fill_color = (*color, 210)
    grace_px   = int(grace_range / map_size * img_w)

    for bx, bz, label in points:
        px, py = blocks_to_pixels(bx, bz, map_size, img_w, img_h)
        draw_point(draw, px, py, dot_radius, grace_px, fill_color,
                   label, f"{bx}, {bz}", label_font, coords_font)

    combined = Image.alpha_composite(img, overlay).convert("RGB")
    combined.save(output_path)
    return points, output_path


# ---------------------------------------------------------------------------
# War result image (mode 2)
# ---------------------------------------------------------------------------

def plot_war_result(
        image_path: str,
        map_size: int,
        spacing: int,
        border_margin: int,
        assignments: dict[str, tuple[int, int, str, str]],
        output_path: str = "map_war_result.png",
        dot_radius: int | None = None,
        font_size: int | None = None,
        grace_range: int = 145,
        excluded: set = None,
        custom_zones: list = None,
        optimize_grid: bool = False,
):
    img = Image.open(image_path).convert("RGBA")
    img_w, img_h = img.size

    if dot_radius is None:
        dot_radius = max(5, int((img_w + img_h) / 2 * 0.005))
    if font_size is None:
        font_size = max(12, int((img_w + img_h) / 2 * 0.015))

    label_font, coords_font, small_font = load_fonts(font_size)

    # All valid points (for faint background)
    points = generate_spawn_points(
        map_size, spacing, border_margin, optimize_grid=optimize_grid
    )
    if excluded:
        points = [(bx, bz, lbl) for bx, bz, lbl in points if lbl not in excluded]
    if custom_zones:
        points = points + custom_zones

    assigned_labels = {lbl for _, _, lbl, _ in assignments.values()}
    bg_points = [(bx, bz, lbl) for bx, bz, lbl in points if lbl not in assigned_labels]

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Faint background grid
    faint = (160, 160, 160, 60)
    for bx, bz, label in bg_points:
        px, py = blocks_to_pixels(bx, bz, map_size, img_w, img_h)
        r = max(2, dot_radius // 2)
        draw.ellipse([px - r, py - r, px + r, py + r], fill=faint)
        draw_text_shadowed(draw, (px, py - r - 2), label, small_font, (160, 160, 160, 80), anchor="mb")

    grace_px = int(grace_range / map_size * img_w)

    for team, (bx, bz, spot_label, color_key) in assignments.items():
        color = VISIBLE_MC_COLORS[color_key]
        px, py = blocks_to_pixels(bx, bz, map_size, img_w, img_h)

        draw.ellipse(
            [px - grace_px, py - grace_px, px + grace_px, py + grace_px],
            outline=(*color, 200),
            width=max(2, dot_radius // 2),
        )
        draw.ellipse(
            [px - dot_radius - 2, py - dot_radius - 2,
             px + dot_radius + 2, py + dot_radius + 2],
            fill=(255, 255, 255, 255),
        )
        draw.ellipse(
            [px - dot_radius, py - dot_radius, px + dot_radius, py + dot_radius],
            fill=(*color, 240),
        )

        gap = dot_radius + 4
        draw_text_shadowed(draw, (px, py - gap), team,       label_font,  (*color, 255),       anchor="mb")
        draw_text_shadowed(draw, (px, py + gap), spot_label, coords_font, (255, 255, 255, 230), anchor="mt")
        draw_text_shadowed(draw, (px, py + gap + int(font_size * 0.8)), f"{bx}, {bz}",
                           coords_font, (200, 200, 200, 200), anchor="mt")

    combined = Image.alpha_composite(img, overlay).convert("RGB")
    combined.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Overlay spawn grid / resolve team war spawns on a Minecraft map image."
    )
    parser.add_argument("--prefix", default="T/",
                        help="File prefix for all event files, e.g. 'c' → EVENT2.png, EVENT2_teams.txt, …")
    parser.add_argument("--size", default=2400, type=int, help="Map size in blocks (side length)")
    parser.add_argument("--spacing", default=300, type=int, help="Distance D between spawn points in blocks")
    parser.add_argument("--border", default=100, type=int, help="Min distance from border in blocks (default: 100)")
    parser.add_argument("--dot-size", default=None, type=int, help="Dot radius in pixels (default: auto)")
    parser.add_argument("--font-size", default=30, type=int, help="Label font size in pixels")
    parser.add_argument("--color", default="255,50,50", help="Grid dot color R,G,B (default: 255,50,50)")
    parser.add_argument("--grace_range", default=145, type=int, help="Grace zone radius in blocks (default: 145)")
    parser.add_argument(
        "--optimize-grid",
        action="store_true",
        help="Shift the spawn grid to maximize the number of equally-spaced zones inside the safe area",
    )
    parser.add_argument("--seed", default=42, type=int, help="ALWAYS SET TO 42")
    args = parser.parse_args()

    # Build all file paths from prefix.
    # If prefix is "EVENT2"     → "EVENT2_map.png"
    # If prefix is "events/"    → "events/map.png"   (trailing slash = directory)
    # If prefix is ""           → "map.png"           (no prefix at all)
    p = args.prefix
    if p and not p.endswith(("/", "\\")):
        def pf(name):
            return f"{p}_{name}"
    elif p:
        def pf(name):
            return f"{p}{name}"
    else:
        def pf(name):
            return name

    image_path = pf("map.png")
    teams_path = pf("teams.txt")
    exclude_path = pf("exclude.txt")
    zones_path = pf("zones.txt")
    output_path = pf("map_with_spawns.png")
    war_out_path = pf("map_war_result.png")

    if p:
        print(f"Prefix:  '{p}'")
        print(f"  map          → {image_path}")
        print(f"  teams        → {teams_path}")
        print(f"  exclude      → {exclude_path}")
        print(f"  zones        → {zones_path}")
        print(f"  grid output  → {output_path}")
        print(f"  war output   → {war_out_path}")
        print()

    # New
    # Auto-create the prefix directory if needed
    if p and p.endswith(("/", "\\")):
        Path(p).mkdir(parents=True, exist_ok=True)

    if not Path(image_path).exists():
        print(f"Error: image file '{image_path}' not found.")
        sys.exit(1)

    try:
        color = tuple(int(v.strip()) for v in args.color.split(","))
        assert len(color) == 3
    except Exception:
        print("Error: --color must be in R,G,B format, e.g. 255,50,50")
        sys.exit(1)

    if args.seed is not None:
        random.seed(args.seed)

    print(f"Map size:      {args.size} x {args.size} blocks  (+-{args.size // 2} from center)")
    print(f"Spacing:       {args.spacing} blocks")
    print(f"Border margin: {args.border} blocks")
    print(f"Grace radius:  {args.grace_range} blocks")
    args.optimize_grid = False
    if args.optimize_grid:
        offset_x, offset_z = find_optimal_grid_offset(
            args.size, args.spacing, args.border
        )
        print(f"Grid placement: optimized (offset {offset_x}, {offset_z})")
    else:
        print("Grid placement: centered on (0, 0)")

    # Load excluded labels
    excluded: set[str] = set()
    if not Path(exclude_path).exists():
        Path(exclude_path).write_text("")
        print(f"Created empty exclude file: {exclude_path}")
    for line in Path(exclude_path).read_text().splitlines():
        for token in line.strip().split():
            if token and not token.startswith("#"):
                excluded.add(token.upper())
    if excluded:
        print(f"Excluded points:   {len(excluded)} ({', '.join(sorted(excluded))})")

    # Automatically exclude zones that are mostly water/beach.
    map_img = Image.open(image_path).convert("RGB")

    grid_points = generate_spawn_points(
        args.size,
        args.spacing,
        args.border,
        optimize_grid=args.optimize_grid,
    )

    auto_excluded = find_water_beach_zones(
        image=map_img,
        points=grid_points,
        map_size=args.size,
        grace_range=args.grace_range,
    )

    new_auto_excluded = auto_excluded - excluded

    if new_auto_excluded:
        with Path(exclude_path).open("a") as f:
            for label in sorted(new_auto_excluded):
                f.write(f"{label}\n")

        excluded.update(new_auto_excluded)

        print(
            f"Auto-excluded {len(new_auto_excluded)} "
            f"water/beach zones: "
            f"{', '.join(sorted(new_auto_excluded))}"
        )

    # Load custom zones
    custom_zones: list[tuple[int, int, str]] = []
    # New
    if not Path(zones_path).exists():
        Path(zones_path).write_text("")
        print(f"Created empty zones file: {zones_path}")
    custom_zones = parse_custom_zones(zones_path)
    if custom_zones:
        print(f"Custom zones:      {len(custom_zones)} ({', '.join(lbl for _, _, lbl in custom_zones)})")

    # Grid image
    points, out = plot_grid(
        image_path=image_path,
        map_size=args.size,
        spacing=args.spacing,
        border_margin=args.border,
        output_path=output_path,
        dot_radius=args.dot_size,
        color=color,
        font_size=args.font_size,
        grace_range=args.grace_range,
        excluded=excluded,
        custom_zones=custom_zones,
        optimize_grid=args.optimize_grid,
    )
    print(f"\nGrid image saved to: {out}")
    print(f"Total spawn points:  {len(points)}  (grid: {len(points) - len(custom_zones)}, custom: {len(custom_zones)})")

    label_to_point = {label: (bx, bz) for bx, bz, label in points}

    # War mode
    # New
    if not Path(teams_path).exists():
        Path(teams_path).write_text("")
        print(f"Created empty teams file: {teams_path}")
        print(f"\nNo teams configured — grid-only mode.")
        print(f"\n{'Label':<8} {'X':>7}  {'Z':>7}")
        print("-" * 25)
        for bx, bz, label in points:
            print(f"{label:<8} {bx:>7}  {bz:>7}")
        return

    teams = parse_teams(teams_path)
    print(f"\nTeams loaded: {len(teams)}")
    for name, color_key, picks in teams:
        picks_str = ', '.join(picks) if picks else '(random)'
        print(f"  {name:<15} [{color_key:<14}]  picks: {picks_str}")

    print("\n── Conflict resolution ──────────────────────────────")
    assignments, log = resolve_spawns(teams, label_to_point, excluded=excluded)
    for line in log:
        print(line)

    print("\n── Results ──────────────────────────────────────────")
    commands = []
    for team, (bx, bz, spot, color_key) in assignments.items():
        cmd = f'/set_team_coordinates_ER {team} {bx} {bz}'
        commands.append(cmd)
        print(f"  {team:<15} [{color_key:<14}]  → {spot:<6} ({bx:>7}, {bz:>7})")

    print("\n── Commands ─────────────────────────────────────────")
    commands.append(f"/mod_gamerules_ER graceRange {args.grace_range}")
    for cmd in commands:
        print(cmd)

    commands_file = Path(war_out_path).stem + "_commands.txt"
    Path(commands_file).write_text("\n".join(commands) + "\n")
    print(f"\nCommands saved to: {commands_file}")

    war_img = plot_war_result(
        image_path=image_path,
        map_size=args.size,
        spacing=args.spacing,
        border_margin=args.border,
        assignments=assignments,
        output_path=war_out_path,
        dot_radius=args.dot_size,
        font_size=args.font_size,
        grace_range=args.grace_range,
        excluded=excluded,
        custom_zones=custom_zones,
        optimize_grid=args.optimize_grid,
    )
    print(f"War result image:  {war_img}")


if __name__ == "__main__":
    main()

# python mc_spawn_plotter.py --image map.png --size 1400 --spacing 250
