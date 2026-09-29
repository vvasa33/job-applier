# Job Hunter

Local-only job hunting assistant. This checkout is the project skeleton: an API, a SQLite connection, and a page that checks the API is up.

`Vasa_Resume.pdf` is the master resume. Nothing in this skeleton reads or writes it.

## Run

```bash
./scripts/dev.sh
```

- API: http://127.0.0.1:8765/health
- UI: http://127.0.0.1:5173

The script creates `backend/.venv` and installs frontend packages on first run. Stop it with Ctrl-C.

API only, after the virtualenv exists:

```bash
backend/.venv/bin/jobhunter api
```

## Tests

```bash
./scripts/test.sh
```

## Configuration

Copy `.env.example` to `.env` and put `JOBHUNTER_LLM_API_KEY` there. `.env` is gitignored. A variable already set in the environment overrides the file.

An optional TOML file is read when `JOBHUNTER_CONFIG` points at it. See `config.example.toml`. Environment variables override the file.

The API binds to loopback only (`127.0.0.1`, `localhost`, or `::1`). The database file is `jobhunter.db` under `~/.local/share/jobhunter` unless `JOBHUNTER_DATA_DIR` is set.
