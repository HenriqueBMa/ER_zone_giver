#!/usr/bin/env python3
"""
Minecraft War Spawn Point Plotter
----------------------------------
Two modes:

1. GRID MODE  — overlay all valid spawn points on the map image.
   python mc_spawn_plotter.py --image map.png --size 4096 --spacing 300

2. WAR MODE   — read team picks, resolve conflicts, generate team image + commands.
   python mc_spawn_plotter.py --image map.png --size 4096 --spacing 300 --teams teams.txt

Teams file format (one team per line, color is REQUIRED, second token):
   TeamName  <color>  A1  C2  C3  D6  B2
   TEF       red      A1  C3  G2  F1
   Nomads    dark_blue

Valid Minecraft 1.21 team colors:
  black, dark_blue, dark_green, dark_aqua, dark_red, dark_purple,
  gold, gray, dark_gray, blue, green, aqua, red, light_purple, yellow, white

Arguments:
    --image     Path to the Minecraft map image
    --size      Map side length in blocks (map goes from -size/2 to +size/2)
    --spacing   Distance D between spawn points in blocks
    --border    Minimum distance from border (default: 100 blocks)
    --output    Output image path (default: map_with_spawns.png)
    --teams     Path to teams text file (activates war mode)
    --war-out   Output image for war results (default: map_war_result.png)
    --dot-size  Radius of the small center dot in pixels (default: auto)
    --font-size Font size in pixels (default: auto)
    --color     Grid dot color as R,G,B (default: 255,50,50)
    --grace_range     Grace zone radius in blocks (default: 145)
    --seed      Random seed for conflict resolution (default: random)
    --exclude   Path to file with grid labels to exclude
"""

import argparse
import random
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow is required. Install it with:  pip install Pillow")
    sys.exit(1)

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

# Brighten very dark colors so they're visible on the map overlay
def _visible(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = rgb
    # If luminance is too low, blend toward a lighter version
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

def generate_spawn_points(
        map_size: int, spacing: int, border_margin: int
) -> list[tuple[int, int, str]]:
    """Returns list of (block_x, block_z, grid_label) for all valid grid points."""
    half = map_size / 2
    safe_min = -half + border_margin
    safe_max = half - border_margin

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
# Coordinate conversion
# ---------------------------------------------------------------------------

def blocks_to_pixels(bx: int, bz: int, map_size: int, img_w: int, img_h: int) -> tuple[int, int]:
    px = int((bx + map_size / 2) / map_size * img_w)
    py = int((bz + map_size / 2) / map_size * img_h)
    return px, py


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
    Parse teams file. Returns [(team_name, mc_color_key, [pick1, pick2, ...]), ...]

    Format per line:
        TeamName  <mc_color>  [pick1 pick2 ...]

    Color is the SECOND token and is required.
    Picks are optional (empty list = fully random).
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
    waiting: list[tuple[str, str]] = []  # (team_name, color_key)

    # Strip excluded labels and build pending dict
    pending: dict[str, tuple[str, list[str]]] = {}  # name → (color_key, picks)
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

    # Random fallback phase
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
):
    img = Image.open(image_path).convert("RGBA")
    img_w, img_h = img.size

    if dot_radius is None:
        dot_radius = max(3, int((img_w + img_h) / 2 * 0.0035))
    if font_size is None:
        font_size = max(11, int((img_w + img_h) / 2 * 0.013))

    label_font, coords_font, _ = load_fonts(font_size)
    points = generate_spawn_points(map_size, spacing, border_margin)
    if excluded:
        points = [(bx, bz, lbl) for bx, bz, lbl in points if lbl not in excluded]

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    fill_color  = (*color, 210)
    white_solid = (255, 255, 255, 255)
    grace_px    = int(grace_range / map_size * img_w)

    for bx, bz, label in points:
        px, py = blocks_to_pixels(bx, bz, map_size, img_w, img_h)
        draw.ellipse(
            [px - dot_radius - 1, py - dot_radius - 1,
             px + dot_radius + 1, py + dot_radius + 1],
            fill=white_solid,
        )
        draw.ellipse(
            [px - dot_radius, py - dot_radius, px + dot_radius, py + dot_radius],
            fill=fill_color,
        )
        draw.ellipse(
            [px - grace_px, py - grace_px, px + grace_px, py + grace_px],
            outline=(*color, 120),
            width=max(1, dot_radius // 2),
        )
        gap = dot_radius + 3
        draw_text_shadowed(draw, (px, py - gap), label, label_font, fill_color, anchor="mb")
        draw_text_shadowed(draw, (px, py + gap), f"{bx}, {bz}", coords_font, (200, 200, 200, 220), anchor="mt")

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
):
    img = Image.open(image_path).convert("RGBA")
    img_w, img_h = img.size

    if dot_radius is None:
        dot_radius = max(5, int((img_w + img_h) / 2 * 0.005))
    if font_size is None:
        font_size = max(12, int((img_w + img_h) / 2 * 0.015))

    label_font, coords_font, small_font = load_fonts(font_size)

    points = generate_spawn_points(map_size, spacing, border_margin)
    if excluded:
        points = [(bx, bz, lbl) for bx, bz, lbl in points if lbl not in excluded]
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
        draw_text_shadowed(draw, (px, py - gap), team, label_font, (*color, 255), anchor="mb")
        draw_text_shadowed(draw, (px, py + gap), spot_label, coords_font, (255, 255, 255, 230), anchor="mt")
        draw_text_shadowed(draw, (px, py + gap + int(font_size * 0.8)), f"{bx}, {bz}", coords_font,
                           (200, 200, 200, 200), anchor="mt")

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
    parser.add_argument("--image",      default="map.png",              help="Path to the map image")
    parser.add_argument("--size",       default=1400,       type=int,   help="Map size in blocks (side length)")
    parser.add_argument("--spacing",    default=300,        type=int,   help="Distance D between spawn points in blocks")
    parser.add_argument("--border",     default=100,        type=int,   help="Min distance from border in blocks (default: 100)")
    parser.add_argument("--output",     default="map_with_spawns.png",  help="Grid output image")
    parser.add_argument("--teams",      default="teams.txt",            help="Path to teams picks file (activates war mode)")
    parser.add_argument("--war-out",    default="map_war_result.png",   help="War result image")
    parser.add_argument("--dot-size",   default=None,       type=int,   help="Dot radius in pixels (default: auto)")
    parser.add_argument("--font-size",  default=30,         type=int,   help="Label font size in pixels")
    parser.add_argument("--color",      default="255,50,50",            help="Grid dot color R,G,B (default: 255,50,50)")
    parser.add_argument("--grace_range",default=145,        type=int,   help="Grace zone radius in blocks (default: 145)")
    parser.add_argument("--seed",       default=None,       type=int,   help="Random seed for conflict resolution")
    parser.add_argument("--exclude",    default="exclude.txt",          help="Path to file with grid labels to exclude")
    args = parser.parse_args()

    if not Path(args.image).exists():
        print(f"Error: image file '{args.image}' not found.")
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

    # Load excluded labels
    excluded: set[str] = set()
    if args.exclude and Path(args.exclude).exists():
        for line in Path(args.exclude).read_text().splitlines():
            for token in line.strip().split():
                if token and not token.startswith("#"):
                    excluded.add(token.upper())
        print(f"Excluded points:   {len(excluded)} ({', '.join(sorted(excluded))})")
    elif args.exclude and not Path(args.exclude).exists():
        print(f"Note: exclude file '{args.exclude}' not found — skipping.")

    # Grid image
    points, out = plot_grid(
        image_path=args.image,
        map_size=args.size,
        spacing=args.spacing,
        border_margin=args.border,
        output_path=args.output,
        dot_radius=args.dot_size,
        color=color,
        font_size=args.font_size,
        grace_range=args.grace_range,
        excluded=excluded,
    )
    print(f"\nGrid image saved to: {out}")
    print(f"Valid spawn points:  {len(points)}")

    label_to_point = {label: (bx, bz) for bx, bz, label in points}

    # War mode
    if not Path(args.teams).exists():
        print(f"\nNo teams file found at '{args.teams}' — grid-only mode.")
        print(f"\n{'Label':<8} {'X':>7}  {'Z':>7}")
        print("-" * 25)
        for bx, bz, label in points:
            print(f"{label:<8} {bx:>7}  {bz:>7}")
        return

    teams = parse_teams(args.teams)
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

    commands_file = Path(args.war_out).stem + "_commands.txt"
    Path(commands_file).write_text("\n".join(commands) + "\n")
    print(f"\nCommands saved to: {commands_file}")

    war_img = plot_war_result(
        image_path=args.image,
        map_size=args.size,
        spacing=args.spacing,
        border_margin=args.border,
        assignments=assignments,
        output_path=args.war_out,
        dot_radius=args.dot_size,
        font_size=args.font_size,
        grace_range=args.grace_range,
        excluded=excluded,
    )
    print(f"War result image:  {war_img}")


if __name__ == "__main__":
    main()

# python mc_spawn_plotter.py --image map.png --size 1400 --spacing 250