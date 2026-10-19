# Later Ledger

Three tools for ordinary money decisions: what a set of deposits would have been worth, a range if that mix keeps going, and how often a pile of money would have lasted.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn pf.server:app --app-dir src --port 8765
```

Open http://127.0.0.1:8765.
