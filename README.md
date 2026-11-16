# deep-strategy

I am building a small real-time strategy game as a testbed for game AI: parametrized
agents play each other and I can watch every match. Inspired by
[DeepRTS](https://github.com/pa-research/deep-rts) (my inspiration and starting reference)
and my earlier project ejaj-boj.

Planning, analyses, decisions and the roadmap live in the parent workspace repo
(`../../`: `AGENTS.md`, `CONTEXT.md`, `docs/general/`, `docs/roadmap/ROADMAP.md`).

## Status

Python reference implementation of M0-M4 (no Rust yet; see decision D26). Not done: M3b
(wood and stone), M3c backpressure load test, M4b/M4c (Rust), M5 search, M6 learning.

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

## Layout

| Path | What |
|------|------|
| `data/rules.toml` | all game rules (costs, hp, counters), as data |
| `lrts/world.py` | the sim: `step(commands) -> events`, `snapshot`, `restore`, `state_hash`, no clock/IO/globals/floats/RNG |
| `lrts/mapgen.py`, `pathfinding.py`, `rules.py` | seeded point-symmetric maps, A*, rules loader |
| `lrts/agents.py` | parametrized agent, spec schema and validation |
| `lrts/match.py` | runs one seeded match, records replays, contains agent crashes |
| `lrts/arena.py`, `evolve.py` | round-robin + Elo + SQLite; evolution strategy over parameters |
| `lrts/server.py` | pacing, WebSocket streaming, per-spectator mailbox backpressure, replay writing |
| `viewer/` | one canvas renderer for live and replay; skin manifests (`skins/`) |
| `specs/` | agent specs (TOML) |

## Rules of the game (v1)

32x32 map, point-symmetric, 4 gold mines (2 near bases, 2 contested). Hall trains workers;
barracks (150 gold, 60 ticks) trains sword, archer, knight, which form a counter cycle
(sword > knight > archer > sword, x1.5 damage). Destroy the enemy hall to win; at tick 3000
the side with the higher hall hp (then army value) wins, an exact tie is a draw.
