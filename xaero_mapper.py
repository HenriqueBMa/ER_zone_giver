"""Teleport a spectator around a square so Xaero's World Map records every chunk.

Usage:
    pip install pyautogui pynput
    python xaero_mapper.py                       # 2600x2600 centred on 0,0
    python xaero_mapper.py --cx 500 --cz -200    # other centre
    python xaero_mapper.py --start 120           # resume from step 120
    python xaero_mapper.py --dry-run             # just print the route

Before starting: be in spectator mode with cheats on, set render distance to 7,
then switch to the Minecraft window during the countdown.
Press F8 at any time to stop (the step number is printed so you can resume).
"""
import argparse
import math
import time

import pyautogui
from pynput import keyboard

stop = False


def on_press(key):
    global stop
    if key == keyboard.Key.f8:
        stop = True


def build_route(cx, cz, size, view_chunks, overlap_chunks):
    """Serpentine grid of chunk-centre positions covering the square.

    Loaded chunks form a circle of radius view_chunks around the player, so a
    grid with spacing <= r*sqrt(2) leaves no gaps; overlap_chunks adds margin.
    """
    step = max(1, math.floor(view_chunks * math.sqrt(2)) - overlap_chunks)
    half = size // 2
    cmin_x, cmax_x = (cx - half) >> 4, (cx + half - 1) >> 4
    cmin_z, cmax_z = (cz - half) >> 4, (cz + half - 1) >> 4

    def axis(lo, hi):
        pts = list(range(lo, hi + 1, step))
        if pts[-1] != hi:
            pts.append(hi)
        return pts

    xs, zs = axis(cmin_x, cmax_x), axis(cmin_z, cmax_z)
    route = []
    for i, z in enumerate(zs):
        row = xs if i % 2 == 0 else list(reversed(xs))
        route.extend((x * 16 + 8, z * 16 + 8) for x in row)
    return route, step


def send_command(cmd):
    pyautogui.press("t")
    time.sleep(0.15)
    pyautogui.write(cmd, interval=0.01)
    time.sleep(0.05)
    pyautogui.press("enter")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cx", type=int, default=0, help="centre X")
    p.add_argument("--cz", type=int, default=0, help="centre Z")
    p.add_argument("--size", type=int, default=2600, help="side length in blocks")
    p.add_argument("--view", type=int, default=7, help="render distance in chunks")
    p.add_argument("--overlap", type=int, default=1, help="extra safety chunks")
    p.add_argument("--y", type=int, default=200, help="teleport height")
    p.add_argument("--wait", type=float, default=4.0, help="seconds to wait per spot")
    p.add_argument("--start", type=int, default=0, help="step to resume from")
    p.add_argument("--countdown", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    route, step = build_route(args.cx, args.cz, args.size, args.view, args.overlap)
    eta = (len(route) - args.start) * (args.wait + 0.5) / 60
    print(f"{len(route)} spots, spacing {step} chunks ({step * 16} blocks), ~{eta:.0f} min")

    if args.dry_run:
        for i, (x, z) in enumerate(route):
            print(i, x, z)
        return

    keyboard.Listener(on_press=on_press, daemon=True).start()
    for s in range(args.countdown, 0, -1):
        print(f"Starting in {s}... focus Minecraft")
        time.sleep(1)

    for i in range(args.start, len(route)):
        if stop:
            print(f"Stopped. Resume with --start {i}")
            return
        x, z = route[i]
        print(f"[{i + 1}/{len(route)}] tp {x} {args.y} {z}")
        send_command(f"/tp @s {x} {args.y} {z}")
        time.sleep(args.wait)
    print("Done. Open Xaero's World Map and export it as PNG.")


if __name__ == "__main__":
    main()
