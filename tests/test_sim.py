"""The determinism contract (M1 exit gate) and the RL-contract defects found in deep-rts."""
import random

import pytest

from lrts.agents import SpecError, load_spec, spec_from_params, validate_params
from lrts.mapgen import generate
from lrts.match import Match
from lrts.rules import Rules
from lrts.world import World

RULES = Rules()
UNITS = ["worker", "sword", "archer", "knight", "bogus"]


def random_commands(rng, tick):
    out = [[], []]
    for p in (0, 1):
        for _ in range(rng.randint(0, 2)):
            r = rng.random()
            if r < 0.5:
                out[p].append({"type": "train", "unit": rng.choice(UNITS)})
            elif r < 0.6:
                out[p].append({"type": "build", "building": rng.choice(["barracks", "hall", "x"])})
            elif r < 0.9:
                out[p].append({"type": "attack_move", "x": rng.randint(-2, 33), "y": rng.randint(0, 31)})
            else:
                out[p].append(rng.choice([None, 5, {"type": 7}, {}]))
    return out


def run_random(seed, ticks=400):
    terrain, bases, mines = generate(seed)
    w = World(RULES, terrain, bases, mines)
    rng = random.Random(seed * 7919)
    hashes = []
    for t in range(ticks):
        w.step(random_commands(rng, t))
        if t % 50 == 0:
            hashes.append(w.state_hash())
    return hashes, w


@pytest.mark.parametrize("seed", range(1, 9))
def test_same_seed_same_hashes(seed):
    assert run_random(seed)[0] == run_random(seed)[0]


def test_different_seeds_differ():
    assert run_random(1)[0] != run_random(2)[0]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_snapshot_restore_continues_identically(seed):
    terrain, bases, mines = generate(seed)
    a = World(RULES, terrain, bases, mines)
    rng = random.Random(seed)
    stream = [random_commands(rng, t) for t in range(300)]
    for c in stream[:150]:
        a.step(c)
    snap = a.snapshot()
    b = World.restore(snap, RULES, terrain, bases, mines)
    assert a.state_hash() == b.state_hash()
    for c in stream[150:]:
        a.step(c)
        b.step(c)
    assert a.state_hash() == b.state_hash()


def test_snapshot_is_independent_copy():
    terrain, bases, mines = generate(1)
    w = World(RULES, terrain, bases, mines)
    snap = w.snapshot()
    h = w.state_hash()
    snap["ents"][0]["hp"] = -5
    snap["gold"][0] = 99999
    assert w.state_hash() == h


def test_observation_cannot_corrupt_state():           # deep-rts: stale/aliased observations
    terrain, bases, mines = generate(1)
    w = World(RULES, terrain, bases, mines)
    h = w.state_hash()
    obs = w.observe(0)
    obs["entities"].clear()
    obs["gold"] = 10 ** 9
    assert w.state_hash() == h


def test_invalid_commands_never_raise():
    terrain, bases, mines = generate(1)
    w = World(RULES, terrain, bases, mines)
    ev = w.step([[None, 3, {"type": "train"}, {"type": "attack_move", "x": "a", "y": 1}], []])
    assert sum(1 for e in ev if e["ev"] == "rejected") == 4


def test_step_does_not_depend_on_wall_time():
    import time
    a = run_random(3, 100)[0]
    time.sleep(0.05)
    assert a == run_random(3, 100)[0]


def test_map_is_point_symmetric_and_connected():
    for seed in range(1, 20):
        t, bases, _mines = generate(seed)
        n = len(t)
        for y in range(n):
            for x in range(n):
                assert t[y][x] == t[n - 1 - y][n - 1 - x]
        assert bases[1] == (n - 1 - bases[0][0], n - 1 - bases[0][1])


def test_full_match_reproducible():
    s = [load_spec("specs/rush.toml"), load_spec("specs/turtle.toml")]
    a = Match(*s, seed=5).run()
    b = Match(*s, seed=5).run()
    assert a == b


def test_tie_is_a_draw_not_player_one():                # ejaj-boj: ties went to player 1
    s = load_spec("specs/rush.toml")
    r = Match(s, s, seed=1).run()
    assert r["winner"] in (-1, 0, 1)
    terrain, bases, mines = generate(1)
    w = World(RULES, terrain, bases, mines)
    w.tick = RULES.game["max_ticks"] - 1
    w.step([[], []])
    assert w.result == {"winner": -1, "reason": "time limit"}


def test_crashing_agent_forfeits_but_server_survives():  # ejaj-boj: exception left engine "running"
    class Boom:
        def act(self, obs):
            raise RuntimeError("bug")
    spec = load_spec("specs/rush.toml")
    m = Match(spec, spec, seed=1)
    m.agents[0] = Boom()
    r = m.run()
    assert r["winner"] == 1 and "forfeit" in r["reason"] and r["error"]["player"] == 0


def test_agent_returning_garbage_forfeits():
    class Garbage:
        def act(self, obs):
            return "train", None
    spec = load_spec("specs/rush.toml")
    m = Match(spec, spec, seed=1)
    m.agents[1] = Garbage()
    assert m.run()["winner"] == 0


def test_spec_validation():
    with pytest.raises(SpecError):
        validate_params({"workers": 99})
    with pytest.raises(SpecError):
        validate_params({"workers": "six"})
    with pytest.raises(SpecError):
        validate_params({"nope": 1})
    with pytest.raises(SpecError):
        validate_params({"w_sword": 0, "w_archer": 0, "w_knight": 0})
    assert validate_params({})["workers"] == 6


def test_economy_and_production_work():
    s = spec_from_params("t", {"attack_size": 60})
    m = Match(s, s, seed=1)
    for _ in range(900):
        m.tick_once()
    kinds = {e["k"] for e in m.world.ents.values() if e["o"] == 0}
    assert {"hall", "barracks", "worker"} <= kinds
    assert any(k in kinds for k in RULES.military)
