"""Run one seeded match between two agent specs. The runner owns pacing and recording;
the sim never sees a clock. A crashing or misbehaving agent forfeits only its own match."""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any

from .agents import make_agent
from .aliases import Command, Event, Spec
from .mapgen import generate
from .rules import Rules
from .world import World


class Match:
    def __init__(self, spec0: Spec, spec1: Spec, seed: int, rules: Rules | None = None, record: bool = False) -> None:
        self.rules = rules or Rules()
        self.specs = [spec0, spec1]
        self.seed = seed
        terrain, bases, mines = generate(seed, self.rules.game["map_size"])
        self.map = {"size": self.rules.game["map_size"], "terrain": terrain, "bases": bases, "mines": mines}
        self.world = World(self.rules, terrain, bases, mines)
        self.agents = [make_agent(spec0, 0, self.rules), make_agent(spec1, 1, self.rules)]
        self.error: dict[str, Any] | None = None
        self.record = record
        self.kinds = sorted(set(self.rules.units) | set(self.rules.buildings) | {"mine"})
        self.frames: list[dict[str, Any]] = []
        self.notes: dict[int, list[str | None]] = {}
        self.finished = False
        self.result: dict[str, Any] | None = None
        self.timing = [0.0, 0.0]   # seconds spent in each agent's act()

    def header(self) -> dict[str, Any]:
        return {"rules_hash": self.rules.hash, "rules_version": self.rules.version, "seed": self.seed,
                "map": self.map, "specs": self.specs, "kinds": self.kinds, "maxhp": self.max_hp(),
                "speed": {u: self.rules.units[u]["speed"] for u in self.rules.units}}

    def max_hp(self) -> dict[str, int]:
        r = self.rules
        out = {u: r.units[u]["hp"] for u in r.units}
        out.update({b: r.buildings[b]["hp"] for b in r.buildings})
        out["mine"] = r.game["mine_amount"]
        return out

    def frame(self, attacks: Iterable[Event] = ()) -> dict[str, Any]:
        w = self.world
        k = {n: i for i, n in enumerate(self.kinds)}
        return {"t": w.tick, "g": list(w.gold),
                "e": [[e["id"], e["o"], k[e["k"]], e["x"], e["y"], e["hp"]]
                      for e in sorted(w.ents.values(), key=lambda e: e["id"])],
                "a": [[a["a"], a["t"]] for a in attacks]}

    def tick_once(self) -> tuple[dict[str, Any], Sequence[str | None]]:
        """Advance one tick. Returns (frame, notes) for live streaming."""
        w = self.world
        cmds: list[list[Command]] = [[], []]
        notes: list[str | None] = [None, None]
        for p in (0, 1):
            if self.error is not None:
                break
            t0 = time.perf_counter()
            try:
                c, n = self.agents[p].act(w.observe(p))
                if not isinstance(c, list):
                    raise TypeError(f"agent returned {type(c).__name__}, expected list")
                cmds[p], notes[p] = c, n
            except Exception as ex:  # noqa: BLE001  deliberate: an agent bug ends this match, never the process
                self.error = {"player": p, "error": f"{type(ex).__name__}: {ex}"}
            self.timing[p] += time.perf_counter() - t0
        if self.error is not None:
            self.result = {"winner": 1 - self.error["player"], "reason": "forfeit: " + self.error["error"]}
            self.finished = True
            return self.frame(), [None, None]
        events = w.step(cmds)
        attacks = [e for e in events if e["ev"] == "attack"]
        fr = self.frame(attacks)
        if self.record:
            self.frames.append(fr)
            if any(notes):
                self.notes[w.tick] = notes
        if w.result is not None:
            self.result = dict(w.result)
            self.finished = True
        return fr, notes

    def run(self) -> dict[str, Any]:
        while not self.finished:
            self.tick_once()
        return self.summary()

    def summary(self) -> dict[str, Any]:
        assert self.result is not None      # only meaningful once the match has finished
        return {"seed": self.seed, "winner": self.result["winner"], "reason": self.result["reason"],
                "ticks": self.world.tick, "hash": self.world.state_hash(), "error": self.error}

    def replay(self) -> dict[str, Any]:
        return {"header": self.header(), "frames": self.frames, "notes": self.notes,
                "result": {**self.summary()}}
