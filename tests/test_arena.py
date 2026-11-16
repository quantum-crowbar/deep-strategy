import random

from lrts.agents import SCHEMA, load_spec, spec_from_params
from lrts.arena import elo, report, run_arena
from lrts.evolve import mutate


def test_elo_is_zero_sum_and_orders_winner_first():
    r = elo([("a", "b", 0)] * 5, ["a", "b"])
    assert r["a"] > 1000 > r["b"]
    assert abs(r["a"] + r["b"] - 2000) < 1e-9


def test_draw_moves_nothing_for_equals():
    r = elo([("a", "b", -1)], ["a", "b"])
    assert r["a"] == r["b"] == 1000


def test_arena_runs_and_reports(tmp_path):
    specs = [load_spec("specs/rush.toml"), load_spec("specs/counter.toml")]
    rows = run_arena(specs, games=2, db=str(tmp_path / "a.sqlite"), procs=1)
    assert len(rows) == 4                      # 2 seeds x 2 sides
    text = report(rows, ["rush", "counter"])
    assert "Elo" in text and "0 with an agent error" in text


def test_mutation_always_produces_valid_params():
    rng = random.Random(1)
    p = spec_from_params("x", {})["params"]
    for _ in range(300):
        p = mutate(p, rng, 0.5)
        spec_from_params("x", p)               # raises SpecError if out of range / wrong type
        assert set(p) == set(SCHEMA)
