"""Seeded, point-symmetric map generation so neither side has a map advantage.

The RNG is a local `random.Random(seed)`: no global state (a lesson from ejaj-boj,
whose determinism needed a global id counter reset and the global `random`).
"""

from __future__ import annotations

import random
from collections import deque

from .aliases import Pos


def _connected(terrain: list[str], size: int, a: Pos, b: Pos) -> bool:
    seen = {a}
    q = deque([a])
    while q:
        x, y = q.popleft()
        if (x, y) == b:
            return True
        for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            n = (x + dx, y + dy)
            if 0 <= n[0] < size and 0 <= n[1] < size and n not in seen and terrain[n[1]][n[0]] != "#":
                seen.add(n)
                q.append(n)
    return False


def generate(seed: int, size: int = 32) -> tuple[list[str], list[Pos], list[Pos]]:
    """Return (terrain rows, base positions, mine positions). Terrain: '.' floor, '#' rock."""
    rng = random.Random(seed)
    last = size - 1
    base0 = (4, 4)
    base1 = (last - 4, last - 4)
    mines = [(7, 4), (last - 7, last - 4)]
    # a contested pair near the middle, mirrored through the centre
    mx, my = rng.randint(11, 15), rng.randint(11, 15)
    mines += [(mx, my), (last - mx, last - my)]
    reserved = set()
    for bx, by in (base0, base1):
        for dx in range(-4, 5):
            for dy in range(-4, 5):
                reserved.add((bx + dx, by + dy))
    reserved.update(mines)
    for mx_, my_ in mines:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                reserved.add((mx_ + dx, my_ + dy))
    for _attempt in range(50):
        grid = [["."] * size for _ in range(size)]
        for _ in range(size * size // 14):
            x, y = rng.randrange(size), rng.randrange(size)
            if (x, y) in reserved:
                continue
            grid[y][x] = "#"
            grid[last - y][last - x] = "#"  # point symmetry
        rows = ["".join(r) for r in grid]
        if _connected(rows, size, base0, base1):
            return rows, [base0, base1], mines
    return ["." * size for _ in range(size)], [base0, base1], mines
