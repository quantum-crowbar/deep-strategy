# deep-strategy: agent instructions

> Harness-agnostic. Tool-specific files only point here.

- Project context and the boundary between this project and the DeepRTS reference: see the metarepo `AGENTS.md` and `CONTEXT.md` two directories up.
- This repo holds **my own development only**. DeepRTS (`../deep-rts`, my inspiration and starting reference) is read-only; do not copy code from it without recording why in the commit message.
- No AI/LLM attribution trailers in commits or PR bodies.
- **Teaching mode applies**: I am learning Rust. Explain choices, show code and the Rust concepts it uses, relate them to C++, take small readable steps, and say what was not compiled or run. Full text in the metarepo `AGENTS.md`.
- Stack: Rust sim core (decided); Python agents/arena/match server and TypeScript viewer (proposed). Design, decisions and glossary: metarepo `docs/general/`. Milestones: metarepo `docs/roadmap/ROADMAP.md`.
- Layout and conventions: **TBD** (milestone M0).
