# deep-strategy

I am building a small real-time strategy game as a testbed for game AI: parametrized
agents play each other and I can watch every match. Inspired by
[DeepRTS](https://github.com/pa-research/deep-rts) (my inspiration and starting reference)
and my earlier project ejaj-boj. My goals, in order: game AI in strategy games, then Rust,
then polishing my experience building real-time systems.

## The plan

The plan is my **[roadmap](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md)**. It sets out three tracks that run through the same
milestones ([AI, real-time systems, Rust](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#three-tracks)), the
[design principles](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#design-principles) the code must obey, and the
[milestones M0 to M7](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones), each with an exit gate that a run can fail. Every
decision, with its status and what would change it, is in the
[decision log](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/general/decisions.md). Planning, the analyses of DeepRTS and ejaj-boj, and the design docs
live in the workspace repo [learning-rts](https://github.com/quantum-crowbar/learning-rts);
this repository holds only the code.

## Where the code stands

Built so far is the Python reference implementation of M0 to M4; Rust is not started
([D26](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/general/decisions.md)). The [Progress table](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#progress) in the roadmap says, per milestone, what is
done, what is partly done and what is not, with the evidence and caveats.
One caveat matters before anything else: the first evolved agent wins by exploiting the
time-limit rule, not by fighting well ([Q11](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/general/decisions.md)).

| Path | What | Roadmap |
|------|------|---------|
| `data/rules.toml`, `lrts/rules.py` | all game rules (costs, hp, counters) as data | [M0](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones), [principles](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#design-principles) |
| `lrts/world.py` | the sim: `step(commands) -> events`, `snapshot`, `restore`, `state_hash`; no clock, I/O, globals, floats or RNG | [M1](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones), [M2](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones) |
| `lrts/mapgen.py`, `lrts/pathfinding.py` | seeded point-symmetric maps, A* | [M1](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones) |
| `lrts/agents.py`, `specs/` | parametrized agent, validated TOML specs, hand-written agents | [M3](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones) |
| `lrts/match.py` | one seeded match, replay recording, agent fault containment | [M1b](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones), [M3](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones) |
| `lrts/arena.py` | round-robin, side swap, SQLite results, Elo | [M3](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones) |
| `lrts/evolve.py`, `specs/evolved.toml` | evolution strategy over agent parameters; its first result | [M4](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones) |
| `lrts/server.py` | pacing, WebSocket streaming, per-spectator backpressure, replay writing | [M1c](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones), [M3c](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones) |
| `viewer/`, `scripts/setup_skin.py` | one canvas renderer for live and replay; skin manifests; CC0 sprite copy script | [M1b](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones), [Viewer](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#viewer), [Sprites](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#sprites) |
| `tests/` | determinism contract, arena, server | [M1](https://github.com/quantum-crowbar/learning-rts/blob/main/docs/roadmap/ROADMAP.md#milestones) exit gate |

Not in this repository yet: wood and stone (M3b), the Rust leaf module and core port
(M4b, M4c), search (M5), learned agents (M6), the optional M7 items.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests                              # determinism + server tests
.venv/bin/python scripts/setup_skin.py                           # optional: CC0 sprites from ../ejaj-boj
.venv/bin/python -m lrts.server specs/rush.toml specs/counter.toml --seed 3   # watch live
.venv/bin/python -m lrts.arena specs/*.toml --games 10 --procs 6              # rated round-robin
.venv/bin/python -m lrts.evolve specs/rush.toml specs/boom.toml specs/turtle.toml specs/counter.toml
```

Open `http://127.0.0.1:8765/`. Finished matches are saved in `replays/` and appear in the
viewer's replay list (`?replay=<file>` opens one directly).

## Rules of the game (v1)

32x32 map, point-symmetric, 4 gold mines (2 near bases, 2 contested). Hall trains workers;
barracks (150 gold, 60 ticks) trains sword, archer, knight, which form a counter cycle
(sword > knight > archer > sword, x1.5 damage). Destroy the enemy hall to win; at tick 3000
the side with the higher hall hp (then army value) wins, an exact tie is a draw.
