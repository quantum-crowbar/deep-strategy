"""Parametrized scripted agent + spec loading/validation (D13, Q02).

An agent spec is a TOML file: a name, a type, and parameters. Parameters are validated
against SCHEMA, so a bad value is rejected when the spec is loaded, never half-way
through a match (ejaj-boj's 18 unvalidated parameters could freeze its engine).

Agents are allowed floats and anything else; only the sim must be integer-only.
This agent uses no randomness: ties are broken by fixed order, so a match is a pure
function of (spec0, spec1, seed).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .aliases import Command, Obs, Row, Spec
from .rules import Rules

# name: (type, min, max, default)
SCHEMA = {
    "workers": (int, 2, 14, 6),            # worker count to hold
    "barracks": (int, 1, 3, 1),            # barracks to build
    "barracks_at": (int, 0, 1000, 120),    # earliest tick to start the first barracks
    "w_sword": (float, 0.0, 1.0, 0.34),    # unit mix weights (relative)
    "w_archer": (float, 0.0, 1.0, 0.33),
    "w_knight": (float, 0.0, 1.0, 0.33),
    "counter_pick": (float, 0.0, 1.0, 0.0),  # 0 = ignore the enemy mix, 1 = build only counters
    "attack_size": (int, 1, 60, 10),       # army size that triggers an attack
    "decision_interval": (int, 1, 60, 10), # ticks between decisions (slow agents are allowed)
    "reserve_gold": (int, 0, 400, 0),      # gold to keep unspent
}

COUNTER = {"sword": "archer", "archer": "knight", "knight": "sword"}  # unit that beats the key


class SpecError(ValueError):
    pass


def validate_params(params: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in params.items():
        if k not in SCHEMA:
            raise SpecError(f"unknown parameter {k!r}")
        typ, lo, hi, _ = SCHEMA[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (typ is int and not isinstance(v, int)):
            raise SpecError(f"{k}: expected {typ.__name__}, got {v!r}")
        if not lo <= v <= hi:
            raise SpecError(f"{k}: {v} outside [{lo}, {hi}]")
        out[k] = typ(v)
    for k, (_, _, _, d) in SCHEMA.items():
        out.setdefault(k, d)
    if out["w_sword"] + out["w_archer"] + out["w_knight"] <= 0:
        raise SpecError("unit weights must not all be zero")
    return out


def load_spec(path: str | Path) -> Spec:
    d = tomllib.loads(Path(path).read_text())
    if d.get("type", "param") != "param":
        raise SpecError(f"unknown agent type {d.get('type')!r}")
    return {"name": d.get("name", Path(path).stem), "type": "param", "params": validate_params(d.get("params", {}))}


def spec_from_params(name: str, params: dict[str, Any]) -> Spec:
    return {"name": name, "type": "param", "params": validate_params(params)}


class ParamAgent:
    def __init__(self, spec: Spec, player: int, rules: Rules) -> None:
        self.p = spec["params"]
        self.player = player
        self.rules = rules
        self.last_order_army = 0
        self.last_order_tick = -999

    def act(self, obs: Obs) -> tuple[list[Command], str | None]:
        """obs -> (commands, note). Called by the match runner every tick; acts on its own interval."""
        t = obs["tick"]
        if t % self.p["decision_interval"] != 0:
            return [], None
        me = self.player
        ents = obs["entities"]
        mine = [e for e in ents if e[1] == me]
        foes = [e for e in ents if e[1] == 1 - me]
        n_workers = sum(1 for e in mine if e[2] == "worker")
        halls = [e for e in mine if e[2] == "hall"]
        barracks = [e for e in mine if e[2] == "barracks"]
        ready_rax = [e for e in barracks if e[6] != "building"]
        queued_workers = sum(e[7] for e in halls)
        army = [e for e in mine if e[2] in self.rules.military]
        cmds, notes = [], []
        gold = obs["gold"] - self.p["reserve_gold"]

        def cost(unit: str) -> int:
            return self.rules.units[unit]["cost"]

        if n_workers + queued_workers < self.p["workers"] and gold >= cost("worker"):
            cmds.append({"type": "train", "unit": "worker"})
            gold -= cost("worker")
            notes.append("worker")
        if len(barracks) < self.p["barracks"] and t >= self.p["barracks_at"] \
                and gold >= self.rules.buildings["barracks"]["cost"]:
            cmds.append({"type": "build", "building": "barracks"})
            gold -= self.rules.buildings["barracks"]["cost"]
            notes.append("barracks")
        if ready_rax:
            unit = self._pick_unit(army, foes)
            slots = sum(5 - e[7] for e in ready_rax)
            while gold >= cost(unit) and slots > 0:
                cmds.append({"type": "train", "unit": unit})
                gold -= cost(unit)
                slots -= 1
                notes.append(unit)
        # military orders
        if halls:
            hx, hy = halls[0][3], halls[0][4]
            threat = [e for e in foes if e[2] in self.rules.military and max(abs(e[3] - hx), abs(e[4] - hy)) <= 9]
            enemy_hall = [e for e in foes if e[2] == "hall"]
            if threat and len(army) < self.p["attack_size"]:
                if t - self.last_order_tick >= 30:
                    cmds.append({"type": "attack_move", "x": hx, "y": hy})
                    self.last_order_tick = t
                    notes.append("defend")
            elif (len(army) >= self.p["attack_size"] and enemy_hall
                  and (len(army) >= self.last_order_army + 3 or t - self.last_order_tick >= 150)):
                cmds.append({"type": "attack_move", "x": enemy_hall[0][3], "y": enemy_hall[0][4]})
                self.last_order_army = len(army)
                self.last_order_tick = t
                notes.append(f"attack({len(army)})")
        return cmds, (", ".join(notes) or None)

    def _pick_unit(self, army: list[Row], foes: list[Row]) -> str:
        w = {"sword": self.p["w_sword"], "archer": self.p["w_archer"], "knight": self.p["w_knight"]}
        cp = self.p["counter_pick"]
        if cp > 0:
            seen = {u: sum(1 for e in foes if e[2] == u) for u in w}
            total = sum(seen.values())
            if total:
                for u, n in seen.items():
                    w[COUNTER[u]] += cp * n / total
        have = {u: sum(1 for e in army if e[2] == u) for u in w}
        # weighted round-robin: build whichever unit is furthest below its target share
        best: tuple[tuple[float, str], str] | None = None
        for u in sorted(w):
            if w[u] <= 0:
                continue
            key = (have[u] / w[u], u)
            if best is None or key < best[0]:
                best = (key, u)
        assert best is not None      # schema guarantees at least one positive weight
        return best[1]


def make_agent(spec: Spec, player: int, rules: Rules) -> ParamAgent:
    return ParamAgent(spec, player, rules)
