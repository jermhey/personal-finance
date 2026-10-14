# Portfolio replay

A small site for replaying a portfolio from deposits, withdrawals, and target mixes, then sketching a forward path.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn pf.server:app --app-dir src --port 8765
```

Open http://127.0.0.1:8765.
