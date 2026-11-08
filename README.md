# remy-finance

REMY Finance is an experimental options-research engine. It borrows ideas from computer chess and records prospective paper trades in an immutable registry. There is **no brokerage code**: every "order" is a paper record only.

- Governing spec: [`docs/spec/spec_v0.5.md`](docs/spec/spec_v0.5.md)
- Current build brief (stages R0–R1): [`docs/handoff/HANDOFF_R0_R1.md`](docs/handoff/HANDOFF_R0_R1.md)
- Decision records: [`docs/decisions/`](docs/decisions/)
- Stage memos: [`docs/memos/`](docs/memos/)

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```

Copy `.env.example` for the variable names. Keep real values in your shell environment or the macOS keychain, never in the repo. Market data, registry databases and logs go under `data/`, which git ignores.
