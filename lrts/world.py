"""The simulation core (Python reference implementation, decision D26).

Contract (the same one the Rust port must meet):
  * `step(commands)` is the only thing that changes the world; it returns events.
  * No wall clock, no I/O, no global state, no floats, no RNG inside `step`.
  * `snapshot()` / `restore()` round-trip exactly; `state_hash()` fingerprints a state.
  * Entities are processed in ascending id order, so iteration order never decides anything.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from .aliases import Command, Entity, Event, Obs, Pos
from .pathfinding import find_path
from .rules import Rules

MILITARY_ST = ("idle",)


def cheb(ax: int, ay: int, bx: int, by: int) -> int:
    return max(abs(ax - bx), abs(ay - by))


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


class World:
    def __init__(self, rules: Rules, terrain: list[str], bases: list[Pos], mines: list[Pos],
                 snap: dict[str, Any] | None = None) -> None:
        self.rules = rules
        self.terrain = terrain
        self.size = rules.game["map_size"]
        self.bases = bases
        self.mine_pos = mines
        if snap is None:
            self._init_fresh()
        else:
            self._load(snap)

    # ---------------------------------------------------------------- setup
    def _init_fresh(self) -> None:
        g = self.rules.game
        self.tick = 0
        self.next_id = 1
        self.gold = [g["start_gold"], g["start_gold"]]
        self.result: dict[str, Any] | None = None
        self.ents: dict[int, Entity] = {}
        for p, (bx, by) in enumerate(self.bases):
            self._spawn_building(p, "hall", bx, by, ready=True)
        for mx, my in self.mine_pos:
            self._new("mine", -1, mx, my, hp=g["mine_amount"])
        for p in (0, 1):
            for _ in range(g["start_workers"]):
                hall = self._hall(p)
                assert hall is not None
                self._spawn_unit(p, "worker", hall)
        self._blocked: set[Pos] | None = None

    def _new(self, kind: str, owner: int, x: int, y: int, hp: int) -> Entity:
        e = {"id": self.next_id, "o": owner, "k": kind, "x": x, "y": y, "hp": hp,
             "cd": 0, "mv": 0, "dest": None, "path": [], "st": "idle", "t": 0, "carry": 0, "q": []}
        self.ents[self.next_id] = e
        self.next_id += 1
        return e

    def _spawn_building(self, owner: int, kind: str, x: int, y: int, ready: bool) -> Entity:
        spec = self.rules.buildings[kind]
        e = self._new(kind, owner, x, y, spec["hp"])
        if not ready:
            e["st"], e["t"] = "building", spec["build_ticks"]
        self._blocked = None
        return e

    def _spawn_unit(self, owner: int, kind: str, building: Entity) -> Entity:
        spot = self._free_adjacent(building["x"], building["y"])
        e = self._new(kind, owner, spot[0], spot[1], self.rules.units[kind]["hp"])
        return e

    # -------------------------------------------------------------- queries
    def _hall(self, p: int) -> Entity | None:
        for e in self.ents.values():
            if e["k"] == "hall" and e["o"] == p:
                return e
        return None

    def blocked(self) -> set[Pos]:
        if self._blocked is None:
            b = set()
            for y, row in enumerate(self.terrain):
                for x, c in enumerate(row):
                    if c == "#":
                        b.add((x, y))
            for e in self.ents.values():
                if e["k"] in self.rules.buildings or e["k"] == "mine":
                    b.add((e["x"], e["y"]))
            self._blocked = b
        return self._blocked

    def _free_adjacent(self, x: int, y: int) -> Pos:
        bl = self.blocked()
        for r in range(1, 6):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    n = (x + dx, y + dy)
                    if 0 <= n[0] < self.size and 0 <= n[1] < self.size and n not in bl:
                        return n
        return (x, y)

    def _is_building(self, e: Entity) -> bool:
        return e["k"] in self.rules.buildings

    def _enemies_of(self, p: int) -> list[Entity]:
        return [e for e in self.ents.values() if e["o"] == 1 - p]

    # ---------------------------------------------------------------- step
    def step(self, commands: list[list[Command]]) -> list[Event]:
        """Advance one tick. `commands` = [list for player 0, list for player 1]."""
        events: list[Event] = []
        if self.result is not None:
            return events
        for p in (0, 1):
            for c in commands[p]:
                self._apply_command(p, c, events)
        for eid in sorted(self.ents):
            e = self.ents.get(eid)
            if e is None:
                continue
            k = e["k"]
            if k == "worker":
                self._worker(e, events)
            elif k in self.rules.units:
                self._military(e, events)
            elif k in self.rules.buildings:
                self._building(e, events)
        self._reap(events)
        self.tick += 1
        self._check_result(events)
        return events

    # ------------------------------------------------------------- commands
    def _reject(self, p: int, c: Any, why: str, events: list[Event]) -> None:
        events.append({"ev": "rejected", "p": p, "why": why})

    def _apply_command(self, p: int, c: Any, events: list[Event]) -> None:
        if not isinstance(c, dict):
            return self._reject(p, c, "not a dict", events)
        t = c.get("type")
        if t == "train":
            u = c.get("unit")
            spec = self.rules.units.get(u)
            if spec is None:
                return self._reject(p, c, "unknown unit", events)
            if self.gold[p] < spec["cost"]:
                return self._reject(p, c, "no gold", events)
            cands = [e for e in self.ents.values()
                     if e["o"] == p and e["k"] == spec["from"] and e["st"] != "building"
                     and len(e["q"]) < self.rules.game["queue_max"]]
            if not cands:
                return self._reject(p, c, "no free building", events)
            b = min(cands, key=lambda e: (len(e["q"]), e["id"]))
            self.gold[p] -= spec["cost"]
            b["q"].append([u, spec["train_ticks"]])
        elif t == "build":
            name = c.get("building")
            if not isinstance(name, str) or name == "hall" or name not in self.rules.buildings:
                return self._reject(p, c, "unknown building", events)
            spec = self.rules.buildings[name]
            if self.gold[p] < spec["cost"]:
                return self._reject(p, c, "no gold", events)
            hall = self._hall(p)
            if hall is None:
                return self._reject(p, c, "no hall", events)
            spot = self._build_spot(hall)
            if spot is None:
                return self._reject(p, c, "no space", events)
            self.gold[p] -= spec["cost"]
            self._spawn_building(p, name, spot[0], spot[1], ready=False)
        elif t == "attack_move":
            x, y = c.get("x"), c.get("y")
            if not (isinstance(x, int) and isinstance(y, int) and 0 <= x < self.size and 0 <= y < self.size):
                return self._reject(p, c, "bad target", events)
            for e in sorted((e for e in self.ents.values() if e["o"] == p and e["k"] in self.rules.military),
                            key=lambda e: e["id"]):
                e["dest"] = [x, y]
                self._route(e, (x, y))
        else:
            self._reject(p, c, "unknown command", events)

    def _build_spot(self, hall: Entity) -> Pos | None:
        bl = self.blocked()
        hx, hy = hall["x"], hall["y"]
        for r in (3, 4, 5):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    n = (hx + dx, hy + dy)
                    if 0 <= n[0] < self.size and 0 <= n[1] < self.size and n not in bl:
                        return n
        return None

    # --------------------------------------------------------------- movement
    def _route(self, e: Entity, goal: Sequence[int]) -> bool:
        """Set e['path'] to an A* route to `goal`. Returns True if a route exists."""
        e["path"] = [list(s) for s in find_path((e["x"], e["y"]), (goal[0], goal[1]), self.size, self.blocked())]
        return bool(e["path"])

    def _advance(self, e: Entity) -> bool:
        """Take one step along e['path'] if the move timer allows. True if moved."""
        if e["mv"] > 0:
            e["mv"] -= 1
            return False
        if not e["path"]:
            return False
        nx, ny = e["path"][0]
        if (nx, ny) in self.blocked():
            e["path"] = []          # something built or died in the way: caller repaths
            return False
        e["x"], e["y"] = nx, ny
        e["path"].pop(0)
        e["mv"] = self.rules.units[e["k"]]["speed"] - 1
        return True

    def _chase_step(self, e: Entity, tx: int, ty: int) -> None:
        """Greedy step toward (tx, ty); fall back to A* if both greedy moves are blocked."""
        if e["mv"] > 0:
            e["mv"] -= 1
            return
        bl = self.blocked()
        dx, dy = tx - e["x"], ty - e["y"]
        horizontal_first = abs(dx) >= abs(dy)
        opts = [(_sign(dx), 0), (0, _sign(dy))] if horizontal_first else [(0, _sign(dy)), (_sign(dx), 0)]
        for sx, sy in opts:
            if (sx or sy) and (e["x"] + sx, e["y"] + sy) not in bl \
                    and 0 <= e["x"] + sx < self.size and 0 <= e["y"] + sy < self.size:
                e["x"] += sx
                e["y"] += sy
                e["mv"] = self.rules.units[e["k"]]["speed"] - 1
                return
        path = find_path((e["x"], e["y"]), (tx, ty), self.size, bl)
        if path:
            e["x"], e["y"] = path[0]
            e["mv"] = self.rules.units[e["k"]]["speed"] - 1

    # ---------------------------------------------------------------- units
    def _military(self, e: Entity, events: list[Event]) -> None:
        spec = self.rules.units[e["k"]]
        if e["cd"] > 0:
            e["cd"] -= 1
        foes = self._enemies_of(e["o"])
        best = None
        for f in foes:
            d = cheb(e["x"], e["y"], f["x"], f["y"])
            key = (d, self._is_building(f), f["id"])
            if best is None or key < best[0]:
                best = (key, f)
        if best is not None:
            d = best[0][0]
            f = best[1]
            if d <= spec["range"]:
                if e["cd"] == 0:
                    dmg = self.rules.damage(e["k"], f["k"]) if f["k"] in self.rules.units else spec["damage"]
                    f["hp"] -= dmg
                    e["cd"] = spec["cooldown"]
                    events.append({"ev": "attack", "a": e["id"], "t": f["id"], "dmg": dmg})
                return
            if d <= self.rules.game["aggro_range"]:
                self._chase_step(e, f["x"], f["y"])
                return
        if e["dest"] is not None:
            if (e["x"], e["y"]) == tuple(e["dest"]):
                e["dest"] = None
            else:
                if not e["path"] and not self._route(e, e["dest"]):
                    e["dest"] = None
                    return
                self._advance(e)

    def _worker(self, e: Entity, events: list[Event]) -> None:
        """Gather loop: idle -> tomine -> mining -> tohall -> idle (re-picks the nearest live mine)."""
        {"idle": self._w_idle, "tomine": self._w_tomine, "mining": self._w_mining,
         "tohall": self._w_tohall}[e["st"]](e, events)

    def _mine_of(self, e: Entity) -> Entity | None:
        m = self.ents.get(e["dest"][0]) if e["dest"] else None
        return m if m is not None and m["k"] == "mine" else None

    def _w_idle(self, e: Entity, _events: list[Event]) -> None:
        mines = [m for m in self.ents.values() if m["k"] == "mine"]
        if not mines or self._hall(e["o"]) is None:
            return
        m = min(mines, key=lambda m: (cheb(e["x"], e["y"], m["x"], m["y"]), m["id"]))
        e["dest"] = [m["id"]]
        e["st"] = "tomine"
        self._route(e, (m["x"], m["y"]))

    def _w_tomine(self, e: Entity, _events: list[Event]) -> None:
        m = self._mine_of(e)
        if m is None:
            e["st"], e["path"], e["dest"] = "idle", [], None
        elif cheb(e["x"], e["y"], m["x"], m["y"]) <= 1:
            e["st"], e["t"], e["path"] = "mining", self.rules.game["gather_ticks"], []
        elif not e["path"] and not self._route(e, (m["x"], m["y"])):
            e["st"] = "idle"
        else:
            self._advance(e)

    def _w_mining(self, e: Entity, _events: list[Event]) -> None:
        e["t"] -= 1
        if e["t"] > 0:
            return
        m = self._mine_of(e)
        hall = self._hall(e["o"])
        if m is None or hall is None:
            e["st"], e["dest"] = "idle", None
            return
        take = min(self.rules.game["gather_load"], m["hp"])
        m["hp"] -= take
        e["carry"] = take
        e["st"] = "tohall"
        self._route(e, (hall["x"], hall["y"]))

    def _w_tohall(self, e: Entity, events: list[Event]) -> None:
        hall = self._hall(e["o"])
        if hall is None:
            e["st"] = "idle"
        elif cheb(e["x"], e["y"], hall["x"], hall["y"]) <= 1:
            self.gold[e["o"]] += e["carry"]
            events.append({"ev": "gold", "p": e["o"], "n": e["carry"]})
            e["carry"] = 0
            e["st"] = "idle"
        elif e["path"] or self._route(e, (hall["x"], hall["y"])):
            self._advance(e)

    def _building(self, e: Entity, events: list[Event]) -> None:
        if e["st"] == "building":
            e["t"] -= 1
            if e["t"] <= 0:
                e["st"], e["t"] = "idle", 0
                events.append({"ev": "built", "id": e["id"], "p": e["o"]})
            return
        if e["q"]:
            e["q"][0][1] -= 1
            if e["q"][0][1] <= 0:
                u = e["q"].pop(0)[0]
                nu = self._spawn_unit(e["o"], u, e)
                events.append({"ev": "spawn", "id": nu["id"], "p": e["o"], "kind": u})

    # ---------------------------------------------------------------- end of tick
    def _reap(self, events: list[Event]) -> None:
        dead = [i for i, e in self.ents.items() if e["hp"] <= 0]
        for i in sorted(dead):
            e = self.ents.pop(i)
            events.append({"ev": "death" if e["k"] != "mine" else "depleted", "id": i, "kind": e["k"], "p": e["o"]})
            if e["k"] in self.rules.buildings or e["k"] == "mine":
                self._blocked = None

    def _army_value(self, p: int) -> int:
        return sum(self.rules.units[e["k"]]["cost"] for e in self.ents.values()
                   if e["o"] == p and e["k"] in self.rules.military)

    def _check_result(self, events: list[Event]) -> None:
        h = [self._hall(0), self._hall(1)]
        if h[0] is None and h[1] is None:
            self.result = {"winner": -1, "reason": "both halls destroyed"}
        elif h[0] is None:
            self.result = {"winner": 1, "reason": "hall destroyed"}
        elif h[1] is None:
            self.result = {"winner": 0, "reason": "hall destroyed"}
        elif self.tick >= self.rules.game["max_ticks"]:
            a = (h[0]["hp"], self._army_value(0))
            b = (h[1]["hp"], self._army_value(1))
            w = 0 if a > b else 1 if b > a else -1   # a tie is a draw, not a win for player 1
            self.result = {"winner": w, "reason": "time limit"}
        if self.result is not None:
            events.append({"ev": "end", **self.result})

    # --------------------------------------------------------------- observation
    def observe(self, p: int) -> Obs:
        """What agent p sees (no fog yet, D21). Rows are copies, so agents cannot corrupt the sim."""
        rows = [(e["id"], e["o"], e["k"], e["x"], e["y"], e["hp"], e["st"], len(e["q"]))
                for e in sorted(self.ents.values(), key=lambda e: e["id"])]
        return {"tick": self.tick, "player": p, "gold": self.gold[p], "enemy_gold": self.gold[1 - p],
                "entities": rows}

    # ---------------------------------------------------------------- snapshot
    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps({
            "tick": self.tick, "next_id": self.next_id, "gold": self.gold, "result": self.result,
            "ents": [self.ents[i] for i in sorted(self.ents)]}))

    def _load(self, snap: dict[str, Any]) -> None:
        self.tick = snap["tick"]
        self.next_id = snap["next_id"]
        self.gold = list(snap["gold"])
        self.result = snap["result"]
        self.ents = {e["id"]: json.loads(json.dumps(e)) for e in snap["ents"]}
        self._blocked = None

    @classmethod
    def restore(cls, snap: dict[str, Any], rules: Rules, terrain: list[str], bases: list[Pos],
                mines: list[Pos]) -> World:
        return cls(rules, terrain, bases, mines, snap=snap)

    def state_hash(self) -> str:
        blob = json.dumps(self.snapshot(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
