"""Arena: round-robin of agent specs, side-swapped seeded matches, SQLite results, Elo.

Usage: python -m lrts.arena specs/*.toml --games 10 [--db arena.sqlite] [--procs 4]

Each pair plays `games` matches per side assignment on seeds 1..games, so both agents
get both sides of every map. A draw scores 0.5. Elo here is the simple sequential
version (Q05 is still open: Bradley-Terry fits a fixed pool better).
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
from multiprocessing import Pool
from typing import Any

from .agents import load_spec
from .aliases import Spec
from .match import Match
from .rules import Rules


def _play(job: tuple[Spec, Spec, int]) -> tuple[Any, ...]:
    s0, s1, seed = job
    r = Match(s0, s1, seed, rules=Rules()).run()
    return (s0["name"], s1["name"], seed, r["winner"], r["reason"], r["ticks"], r["hash"], json.dumps(r["error"]))


def elo(results: list[tuple[str, str, int]], names: list[str], k: float = 24, base: float = 1000.0) -> dict[str, float]:
    rating = {n: base for n in names}
    for a, b, winner in results:
        score_a = 1.0 if winner == 0 else 0.0 if winner == 1 else 0.5
        ea = 1 / (1 + 10 ** ((rating[b] - rating[a]) / 400))
        rating[a] += k * (score_a - ea)
        rating[b] += k * ((1 - score_a) - (1 - ea))
    return rating


def run_arena(specs: list[Spec], games: int = 10, db: str | None = None, procs: int = 1) -> list[tuple[Any, ...]]:
    jobs = []
    for a, b in itertools.combinations(specs, 2):
        for seed in range(1, games + 1):
            jobs.append((a, b, seed))
            jobs.append((b, a, seed))   # side swap
    if procs > 1:
        with Pool(procs) as pool:
            rows = pool.map(_play, jobs, chunksize=4)
    else:
        rows = [_play(j) for j in jobs]
    if db:
        con = sqlite3.connect(db)
        con.execute("""CREATE TABLE IF NOT EXISTS matches(
            p0 TEXT, p1 TEXT, seed INTEGER, winner INTEGER, reason TEXT, ticks INTEGER, hash TEXT, error TEXT)""")
        con.executemany("INSERT INTO matches VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
    return rows


def report(rows: list[tuple[Any, ...]], names: list[str]) -> str:
    ratings = elo([(r[0], r[1], r[3]) for r in rows], names)
    wins = {(a, b): 0.0 for a in names for b in names if a != b}
    played = {(a, b): 0 for a in names for b in names if a != b}
    for p0, p1, _s, w, *_ in rows:
        played[(p0, p1)] += 1
        played[(p1, p0)] += 1
        if w == 0:
            wins[(p0, p1)] += 1
        elif w == 1:
            wins[(p1, p0)] += 1
        else:
            wins[(p0, p1)] += 0.5
            wins[(p1, p0)] += 0.5
    out = ["Elo (sequential, K=24):"]
    for n, r in sorted(ratings.items(), key=lambda kv: -kv[1]):
        out.append(f"  {n:<14}{r:7.1f}")
    out.append("")
    out.append("Win rate of ROW against COLUMN:")
    out.append(" " * 14 + "".join(f"{n[:9]:>10}" for n in names))
    for a in names:
        cells = "".join(f"{(wins[(a, b)] / played[(a, b)]):>10.2f}" if a != b else f"{'-':>10}" for b in names)
        out.append(f"{a:<14}{cells}")
    errors = sum(1 for r in rows if r[7] != "null")
    out.append(f"\n{len(rows)} matches, {errors} with an agent error, "
               f"{sum(1 for r in rows if r[3] == -1)} draws, "
               f"{sum(1 for r in rows if r[4] == 'time limit')} decided by time limit")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--db")
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    specs = [load_spec(p) for p in a.specs]
    rows = run_arena(specs, a.games, a.db, a.procs)
    print(report(rows, [s["name"] for s in specs]))


if __name__ == "__main__":
    main()
