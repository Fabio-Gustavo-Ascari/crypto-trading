# Crypto Trading Harness

Local, governed research and paper-trading infrastructure for crypto assets.

The Python harness owns policy enforcement, state, audit history, and paper execution. An AI decision provider may propose a typed decision, but it cannot bypass the deterministic risk engine.

## MVP status

The safe core is being built incrementally. It is manually started and stopped; a fixed nightly schedule is intentionally not part of this first version.

## Local setup

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Copy `.env.example` to `.env` only when an optional data provider needs credentials. Never add secrets, local `AGENTS.md`, SQLite databases, or runtime logs to Git.
