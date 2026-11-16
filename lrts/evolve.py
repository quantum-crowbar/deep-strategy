"""Parameter optimisation (M4): a (mu + lambda) evolution strategy over agent parameters.

Fitness = mean score (win 1, draw 0.5) against a fixed pool of hand-written agents, on
both sides of several seeded maps. Simple on purpose: with 10 parameters and 0.6 s matches,
a plain ES is enough to see whether search over the spec space finds anything. CMA-ES
(Q-list) is the upgrade if this plateaus.

Usage: python -m lrts.evolve specs/rush.toml specs/boom.toml ... --gens 10 --out specs/evolved.toml
(the listed specs are the opponent pool)
"""
import argparse
import json
import random
from multiprocessing import Pool
from pathlib import Path

from .agents import SCHEMA, load_spec, spec_from_params
from .match import Match
from .rules import Rules


def _score(args):
    spec, opp, seed, side = args
    pair = (spec, opp) if side == 0 else (opp, spec)
    w = Match(*pair, seed, rules=Rules()).run()["winner"]
    if w == -1:
        return 0.5
    return 1.0 if w == side else 0.0


def evaluate(pool, specs, opponents, seeds):
    jobs = [(s, o, seed, side) for s in specs for o in opponents for seed in seeds for side in (0, 1)]
    res = pool.map(_score, jobs, chunksize=8)
    per = len(opponents) * len(seeds) * 2
    return [sum(res[i * per:(i + 1) * per]) / per for i in range(len(specs))]


def mutate(params, rng, strength):
    out = dict(params)
    for k, (typ, lo, hi, _d) in SCHEMA.items():
        if rng.random() < 0.4:
            step = rng.gauss(0, strength * (hi - lo))
            v = out[k] + step
            out[k] = min(hi, max(lo, round(v) if typ is int else v))
    if out["w_sword"] + out["w_archer"] + out["w_knight"] <= 0:
        out["w_sword"] = 0.3
    return out


def write_toml(path, name, params, note):
    lines = [f'name = "{name}"', 'type = "param"', f"# {note}", "[params]"]
    for k, v in params.items():
        lines.append(f"{k} = {round(v, 4) if isinstance(v, float) else v}")
    Path(path).write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pool", nargs="+")
    ap.add_argument("--gens", type=int, default=10)
    ap.add_argument("--mu", type=int, default=3)
    ap.add_argument("--lam", type=int, default=12)
    ap.add_argument("--seeds", type=int, default=3, help="maps per opponent per generation")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--rng", type=int, default=0)
    ap.add_argument("--out", default="specs/evolved.toml")
    ap.add_argument("--log", default=None)
    a = ap.parse_args()
    rng = random.Random(a.rng)
    opponents = [load_spec(p) for p in a.pool]
    parents = [spec_from_params("p", {})["params"]] + \
              [mutate(spec_from_params("p", {})["params"], rng, 0.3) for _ in range(a.mu - 1)]
    log = []
    with Pool(a.procs) as pool:
        for g in range(a.gens):
            seeds = [1000 + g * 10 + i for i in range(a.seeds)]       # fresh maps each generation
            strength = 0.25 * (1 - g / max(1, a.gens)) + 0.05
            kids = [mutate(rng.choice(parents), rng, strength) for _ in range(a.lam)]
            cands = parents + kids
            fit = evaluate(pool, [spec_from_params("c", c) for c in cands], opponents, seeds)
            ranked = sorted(zip(fit, range(len(cands)), strict=True), key=lambda t: -t[0])
            parents = [cands[i] for _, i in ranked[:a.mu]]
            line = f"gen {g + 1}/{a.gens}: best {ranked[0][0]:.3f}, mean {sum(fit) / len(fit):.3f}"
            print(line, flush=True)
            log.append({"gen": g + 1, "best": ranked[0][0], "mean": sum(fit) / len(fit), "params": parents[0]})
        best = spec_from_params("evolved", parents[0])
        # validation on maps never seen during evolution
        val = evaluate(pool, [best], opponents, list(range(1, 21)))[0]
        print(f"validation (20 unseen maps x {len(opponents)} opponents x 2 sides): {val:.3f}")
        base = [evaluate(pool, [o], [x for x in opponents if x is not o], list(range(1, 11)))[0] for o in opponents]
        scores = {o["name"]: round(b, 3) for o, b in zip(opponents, base, strict=True)}
        print("hand-written agents vs the others (10 maps):", scores)
    write_toml(a.out, "evolved", best["params"], f"ES, {a.gens} gens, validation score {val:.3f}")
    if a.log:
        Path(a.log).write_text(json.dumps(log, indent=1))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
