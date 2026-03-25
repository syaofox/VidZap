# AGENTS.md — NiceVid

## Project overview

NiceVid is a Python 3.13 web application for downloading videos via yt-dlp, built with NiceGUI (FastAPI-based). It supports multi-site video extraction, format selection, batch download, cookie management, and a download history page.

## Project structure

```
src/
  main.py                 # Entry point: NiceGUI app setup, routes
  core/
    db.py                 # SQLite database (downloads, cookies tables)
    ytdlp_handler.py      # yt-dlp wrapper: extract_info, start_download, format logic
    cookie_manager.py     # Cookie file + DB management per domain
    download_queue.py     # Download queue: same-origin sequential, cross-origin parallel
    version.py            # App version from pyproject.toml
  pages/
    home.py               # Main page: URL input, analysis, format selection, download
    history.py            # Download history with list/grid view, retry, preview
    settings.py           # Cookie management page
  components/             # (reserved for shared UI components)
```

Runtime artifacts: `database.sqlite`, `downloads/`, `cookies/`, `.nicegui/` — all gitignored.

## Commands

```bash
# Run the app
uv run python src/main.py

# Lint (ruff)
make lint                # or: uv run ruff check .

# Format (ruff)
make format              # or: uv run ruff format .

# Type check (mypy)
make type-check          # or: uv run mypy .

# Sync dependencies
make sync                # or: uv run sync

# Docker
docker compose up -d     # Production deployment
docker compose build     # Rebuild image
```

### Single-file commands

Lint/type-check specific files for faster feedback:
```bash
# Lint single file
uv run ruff check src/core/download_queue.py
uv run ruff check src/pages/history.py

# Type-check single file
uv run mypy src/core/ytdlp_handler.py
uv run mypy src/pages/settings.py
```

### Testing

No test framework is configured yet. When adding tests:
```bash
# Install pytest
uv add --dev pytest

# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_download_queue.py

# Run tests matching a pattern
uv run pytest -k "download"
```

## Code style

### Python version & syntax

- Target Python 3.13. Use modern syntax: `str | None` (not `Optional[str]`), `dict` (not `Dict`), `list` (not `List`).
- Line length: 100 characters (ruff config in `pyproject.toml`).
- Ruff rules: `E`, `F`, `I`, `N`, `W`, `UP` (pyupgrade).
- Enable PyCharm/VSCode inspection for `from __future__ import annotations` to avoid forward reference issues.

### Imports

- Order enforced by ruff `I` rule: stdlib → third-party → local.
- Use absolute imports from `core` and `pages` (the `src/` dir is added to `sys.path` in `main.py`).
- Example:
  ```python
  import os
  from pathlib import Path

  from nicegui import ui

  from core.db import get_connection
  ```

## Code style (continued)

### Naming

- `snake_case` for functions, variables, module-level constants.
- `UPPER_CASE` for module-level constants (e.g., `DOWNLOADS_DIR`, `DB_PATH`).
- Private functions prefixed with `_` (e.g., `_extract_sync`, `_download_sync`).
- NiceGUI page modules export a `render()` function.

### Types

- Add type hints to all function signatures (parameters and return types).
- Use `dict | None` for optional parameters, not `Optional`.
- `Callable` from `collections.abc`, not `typing`.
- `dict` return types from SQLite: `list[dict]` via `[dict(row) for row in rows]`.
- mypy configured with `warn_return_any=true`, `disallow_untyped_defs=false`.

### Error handling

- Wrap I/O and external calls in try/except. Catch broad `Exception` for yt-dlp operations (it raises varied errors).
- Use `finally` for cleanup (e.g., re-enabling UI buttons).
- Print tracebacks with `traceback.print_exc()` for debugging server-side errors.

### Security

- Never log or expose secrets (API keys, tokens, passwords).
- Cookie files stored in `cookies/` directory are user-specific; never commit them.
- Use environment variables for configuration (`NICEVID_STORAGE_SECRET`, etc.).
- Validate all user inputs before processing.

### NiceGUI patterns

- UI built declaratively with context managers: `with ui.card():`, `with ui.row():`.
- Thread offloading: `asyncio.get_event_loop().run_in_executor(None, sync_fn)`.
- Client storage: `app.storage.user[key]` with `storage_secret` set in `ui.run()`.
- Timers: `ui.timer(interval, callback)` for polling; `.deactivate()` to stop.
- Refreshable UI: `@ui.refreshable` decorator for auto-rebuilding sections.

### Download concurrency

- All downloads go through `core.download_queue.download_queue` (global singleton).
- Same-origin downloads (same domain) run sequentially; different origins run in parallel with no limit.
- Use `await download_queue.enqueue(...)` instead of calling `start_download` directly.
- The queue manages `asyncio.Queue` per origin with dedicated worker coroutines.
- **Cancellation**: `await download_queue.cancel(download_id)` sets an `asyncio.Event` that the yt-dlp progress hook checks; raising `DownloadCancelledError` to abort. The `finally` block in the worker cleans up tracking dicts.
- Always pass a `progress_callback` when enqueueing retries so the history page progress bar updates (`_download_progress` dict).

### NiceGUI client lifecycle

- Timer callbacks (e.g., `refresh_active`) must check `ui.context.client._deleted` before modifying UI; if deleted, deactivate the timer and return early. Wrap in `try/except` to handle missing context.
- Confirmation dialogs: use `ui.dialog()` + `ui.card()` context managers with `on_click` buttons calling a helper; do not use `dialog.on_submit` (not available in NiceGUI 3.9).

### Database

- SQLite via `core.db.get_connection()` context manager (auto-commit, auto-close).
- Schema changes: add `ALTER TABLE` in `init_db()` wrapped in try/except for idempotency.
- Never commit `database.sqlite` — it's gitignored.

## Deployment

- Docker multi-stage build: `Dockerfile` uses `python:3.13-slim`, installs `ffmpeg` and `uv`.
- Runtime runs as non-root user `nicevid` (UID 1000).
- Environment variables: `NICEVID_DATA_DIR`, `NICEVID_STORAGE_SECRET`, `NICEVID_RELOAD`.
- Volumes: `./downloads`, `./cookies`, `./data` mounted for persistence.
- `docker compose up -d` for production; `docker compose build` to rebuild.
- The `NICEVID_STORAGE_SECRET` value in `docker-compose.yml` must be changed before production use.

## Key dependencies

| Package     | Purpose                          |
|-------------|----------------------------------|
| nicegui 3.9 | Web UI framework (Vue + Quasar)  |
| yt-dlp      | Video extraction & download      |
| fastapi     | HTTP routes (via NiceGUI)        |
| pydantic    | Data validation                  |
