"""The simulation core (Python reference implementation, decision D26).

Contract (the same one the Rust port must meet):
  * `step(commands)` is the only thing that changes the world; it returns events.
  * No wall clock, no I/O, no global state, no floats, no RNG inside `step`.
  * `snapshot()` / `restore()` round-trip exactly; `state_hash()` fingerprints a state.
  * Entities are processed in ascending id order, so iteration order never decides anything.
"""
import hashlib
import json

from .pathfinding import find_path

MILITARY_ST = ("idle",)


def cheb(ax, ay, bx, by):
    return max(abs(ax - bx), abs(ay - by))


def _sign(v):
    return (v > 0) - (v < 0)


class World:
    def __init__(self, rules, terrain, bases, mines, snap=None):
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
    def _init_fresh(self):
        g = self.rules.game
        self.tick = 0
        self.next_id = 1
        self.gold = [g["start_gold"], g["start_gold"]]
        self.result = None
        self.ents = {}
        for p, (bx, by) in enumerate(self.bases):
            self._spawn_building(p, "hall", bx, by, ready=True)
        for mx, my in self.mine_pos:
            self._new("mine", -1, mx, my, hp=g["mine_amount"])
        for p in (0, 1):
            for _ in range(g["start_workers"]):
                self._spawn_unit(p, "worker", self._hall(p))
        self._blocked = None

    def _new(self, kind, owner, x, y, hp):
        e = {"id": self.next_id, "o": owner, "k": kind, "x": x, "y": y, "hp": hp,
             "cd": 0, "mv": 0, "dest": None, "path": [], "st": "idle", "t": 0, "carry": 0, "q": []}
        self.ents[self.next_id] = e
        self.next_id += 1
        return e

    def _spawn_building(self, owner, kind, x, y, ready):
        spec = self.rules.buildings[kind]
        e = self._new(kind, owner, x, y, spec["hp"])
        if not ready:
            e["st"], e["t"] = "building", spec["build_ticks"]
        self._blocked = None
        return e

    def _spawn_unit(self, owner, kind, building):
        spot = self._free_adjacent(building["x"], building["y"])
        e = self._new(kind, owner, spot[0], spot[1], self.rules.units[kind]["hp"])
        return e

    # -------------------------------------------------------------- queries
    def _hall(self, p):
        for e in self.ents.values():
            if e["k"] == "hall" and e["o"] == p:
                return e
        return None

    def blocked(self):
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

    def _free_adjacent(self, x, y):
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

    def _is_building(self, e):
        return e["k"] in self.rules.buildings

    def _enemies_of(self, p):
        return [e for e in self.ents.values() if e["o"] == 1 - p]

    # ---------------------------------------------------------------- step
    def step(self, commands):
        """Advance one tick. `commands` = [list for player 0, list for player 1]."""
        events = []
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
    def _reject(self, p, c, why, events):
        events.append({"ev": "rejected", "p": p, "why": why})

    def _apply_command(self, p, c, events):
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
            spec = self.rules.buildings.get(name)
            if spec is None or name == "hall":
                return self._reject(p, c, "unknown building", events)
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
            bl = self.blocked()
            for e in sorted((e for e in self.ents.values() if e["o"] == p and e["k"] in self.rules.military),
                            key=lambda e: e["id"]):
                e["dest"] = [x, y]
                e["path"] = [list(s) for s in find_path((e["x"], e["y"]), (x, y), self.size, bl)]
        else:
            self._reject(p, c, "unknown command", events)

    def _build_spot(self, hall):
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
    def _advance(self, e):
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

    def _chase_step(self, e, tx, ty):
        """Greedy step toward (tx, ty); fall back to A* if both greedy moves are blocked."""
        if e["mv"] > 0:
            e["mv"] -= 1
            return
        bl = self.blocked()
        dx, dy = tx - e["x"], ty - e["y"]
        opts = []
        if abs(dx) >= abs(dy):
            opts = [(_sign(dx), 0), (0, _sign(dy))]
        else:
            opts = [(0, _sign(dy)), (_sign(dx), 0)]
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
    def _military(self, e, events):
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
                if not e["path"]:
                    e["path"] = [list(s) for s in
                                 find_path((e["x"], e["y"]), tuple(e["dest"]), self.size, self.blocked())]
                    if not e["path"]:
                        e["dest"] = None
                        return
                self._advance(e)

    def _worker(self, e, events):
        g = self.rules.game
        st = e["st"]
        if st == "idle":
            mines = [m for m in self.ents.values() if m["k"] == "mine"]
            if not mines or self._hall(e["o"]) is None:
                return
            m = min(mines, key=lambda m: (cheb(e["x"], e["y"], m["x"], m["y"]), m["id"]))
            e["dest"] = [m["id"]]
            e["st"] = "tomine"
            e["path"] = [list(s) for s in find_path((e["x"], e["y"]), (m["x"], m["y"]), self.size, self.blocked())]
        elif st == "tomine":
            m = self.ents.get(e["dest"][0]) if e["dest"] else None
            if m is None or m["k"] != "mine":
                e["st"], e["path"], e["dest"] = "idle", [], None
                return
            if cheb(e["x"], e["y"], m["x"], m["y"]) <= 1:
                e["st"], e["t"], e["path"] = "mining", g["gather_ticks"], []
                return
            if not e["path"]:
                e["path"] = [list(s) for s in find_path((e["x"], e["y"]), (m["x"], m["y"]), self.size, self.blocked())]
                if not e["path"]:
                    e["st"] = "idle"
                    return
            self._advance(e)
        elif st == "mining":
            e["t"] -= 1
            if e["t"] <= 0:
                m = self.ents.get(e["dest"][0]) if e["dest"] else None
                if m is None or m["k"] != "mine":
                    e["st"], e["dest"] = "idle", None
                    return
                take = min(g["gather_load"], m["hp"])
                m["hp"] -= take
                e["carry"] = take
                e["st"] = "tohall"
                hall = self._hall(e["o"])
                if hall is None:
                    e["st"] = "idle"
                    return
                e["path"] = [list(s) for s in find_path((e["x"], e["y"]), (hall["x"], hall["y"]), self.size, self.blocked())]
        elif st == "tohall":
            hall = self._hall(e["o"])
            if hall is None:
                e["st"] = "idle"
                return
            if cheb(e["x"], e["y"], hall["x"], hall["y"]) <= 1:
                self.gold[e["o"]] += e["carry"]
                events.append({"ev": "gold", "p": e["o"], "n": e["carry"]})
                e["carry"] = 0
                e["st"] = "idle"      # re-picks the nearest live mine next tick
                return
            if not e["path"]:
                e["path"] = [list(s) for s in find_path((e["x"], e["y"]), (hall["x"], hall["y"]), self.size, self.blocked())]
                if not e["path"]:
                    return
            self._advance(e)

    def _building(self, e, events):
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
    def _reap(self, events):
        dead = [i for i, e in self.ents.items() if e["hp"] <= 0]
        for i in sorted(dead):
            e = self.ents.pop(i)
            events.append({"ev": "death" if e["k"] != "mine" else "depleted", "id": i, "kind": e["k"], "p": e["o"]})
            if e["k"] in self.rules.buildings or e["k"] == "mine":
                self._blocked = None

    def _army_value(self, p):
        return sum(self.rules.units[e["k"]]["cost"] for e in self.ents.values()
                   if e["o"] == p and e["k"] in self.rules.military)

    def _check_result(self, events):
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
    def observe(self, p):
        """What agent p sees (no fog yet, D21). Rows are copies, so agents cannot corrupt the sim."""
        rows = [(e["id"], e["o"], e["k"], e["x"], e["y"], e["hp"], e["st"], len(e["q"]))
                for e in sorted(self.ents.values(), key=lambda e: e["id"])]
        return {"tick": self.tick, "player": p, "gold": self.gold[p], "enemy_gold": self.gold[1 - p],
                "entities": rows}

    # ---------------------------------------------------------------- snapshot
    def snapshot(self):
        return json.loads(json.dumps({
            "tick": self.tick, "next_id": self.next_id, "gold": self.gold, "result": self.result,
            "ents": [self.ents[i] for i in sorted(self.ents)]}))

    def _load(self, snap):
        self.tick = snap["tick"]
        self.next_id = snap["next_id"]
        self.gold = list(snap["gold"])
        self.result = snap["result"]
        self.ents = {e["id"]: json.loads(json.dumps(e)) for e in snap["ents"]}
        self._blocked = None

    @classmethod
    def restore(cls, snap, rules, terrain, bases, mines):
        return cls(rules, terrain, bases, mines, snap=snap)

    def state_hash(self):
        blob = json.dumps(self.snapshot(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
