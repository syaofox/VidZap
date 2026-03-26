# AGENTS.md — NiceVid

## Project overview

NiceVid is a Python 3.13 web application for downloading videos via yt-dlp, built with NiceGUI (FastAPI-based). It supports multi-site video extraction, format selection, batch download, cookie management, Douyin note (image+video slideshow) extraction via Playwright, and a download history page.

## Project structure

```
src/
  main.py                 # Entry point: NiceGUI app setup, routes
  core/
    db.py                 # SQLite database (downloads, cookies tables)
    ytdlp_handler.py      # yt-dlp wrapper: extract_info, start_download, format logic
    cookie_manager.py     # Cookie file + DB management per domain
    download_queue.py     # Download queue: same-origin sequential, cross-origin parallel
    douyin_note.py        # Douyin note extraction (Playwright + Xvfb) and download
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
uv run python src/main.py        # Run the app
make lint                         # or: uv run ruff check .
make format                       # or: uv run ruff format .
make type-check                   # or: uv run mypy .
make sync                         # or: uv run sync
make playwright-setup             # Install Chromium + system deps (devcontainer)
make post-start                   # Start Xvfb :99 (devcontainer)
docker compose up -d              # Production deployment
docker compose build              # Rebuild image
```

Single-file lint/type-check for faster feedback:
```bash
uv run ruff check src/core/douyin_note.py
uv run mypy src/core/download_queue.py
```

Testing: No test framework configured yet. To add: `uv add --dev pytest` then `uv run pytest tests/`.

## Code style

- **Python 3.13**: Use `str | None` (not `Optional`), `dict`/`list` (not `Dict`/`List`), `Callable` from `collections.abc`.
- **Line length**: 100 chars. Ruff rules: `E`, `F`, `I`, `N`, `W`, `UP`.
- **Imports**: stdlib → third-party → local (enforced by ruff `I`). Use absolute imports: `from core.db import get_connection`.
- **Naming**: `snake_case` functions/variables, `UPPER_CASE` constants, `_prefix` private functions. Page modules export `render()`.
- **Types**: Type hints on all function signatures. `dict | None` for optional params. mypy: `warn_return_any=true`, `disallow_untyped_defs=false`.
- **Error handling**: try/except for I/O and external calls. Catch broad `Exception` for yt-dlp. Use `finally` for cleanup. Log tracebacks with `traceback.print_exc()`.
- **Security**: Never log secrets. Cookie files are gitignored. Use env vars for config. Validate all user inputs.

## Key patterns

### NiceGUI
- Declarative UI with context managers: `with ui.card():`, `with ui.row():`.
- Thread offloading: `asyncio.get_event_loop().run_in_executor(None, sync_fn)`.
- Client storage: `app.storage.user[key]` with `storage_secret` in `ui.run()`.
- Timers: `ui.timer(interval, callback)` for polling; `.deactivate()` to stop.
- Timer callbacks must check `ui.context.client._deleted` before modifying UI.
- Dialogs: `ui.dialog()` + `ui.card()` context managers; do not use `dialog.on_submit`.

### Download concurrency
- All downloads go through `core.download_queue.download_queue` (global singleton).
- Same-origin downloads run sequentially; different origins run in parallel.
- Use `await download_queue.enqueue(...)` — never call `start_download` directly.
- Cancellation: `await download_queue.cancel(download_id)` sets an `asyncio.Event`.
- Always pass `progress_callback` when enqueueing retries for history page updates.

### Cookie domain normalization (`cookie_manager.py`)
- `normalize_domain(domain)`: strips `www.` prefix, port, lowercases. `www.youtube.com:443` → `youtube.com`.
- `extract_domain_from_input(text)`: accepts raw domain or full URL, returns normalized domain.
- `is_valid_domain(domain)`: validates domain format (min 2 segments, valid chars).
- `get_cookie_for_url(url)` normalizes the URL domain then matches with `.{domain}` suffix (subdomain-aware).
- `save_cookie()` auto-normalizes before persisting.

### Download retry chain (`ytdlp_handler.py`)
- `_download_sync()` implements a 5-level fallback: cookie+format+subtitles → no subtitles → auto format → no cookie → no cookie+auto format.
- `_is_format_error()`: detects "Requested format is not available".
- `_is_subtitle_error()`: detects "Unable to download video subtitles" (429 rate limit).
- `_strip_subtitle_opts()`: removes `writesubtitles`/`writeautomaticsub`/`subtitleslangs` from opts.
- Subtitle downloads use `sleep_interval_subtitles = 1` (1s between each) to avoid 429.

### Douyin note extraction (`douyin_note.py`)
- Uses Playwright with Xvfb (non-headless) to avoid Douyin bot detection.
- `_ensure_xvfb()` auto-starts Xvfb on `:99` and sets `DISPLAY` env var.
- Visits Douyin homepage first to acquire fresh `__ac_signature` anti-bot cookies.
- Intercepts API responses (`aweme_list`) for structured image+video data; falls back to DOM extraction.
- `extract_note_images()` returns `image_urls` + `video_urls`.
- `download_note_images()` downloads all media to a directory; `file_path` in DB is a directory (not file).
- Uses `playwright-stealth` for additional anti-detection.

### Database
- SQLite via `core.db.get_connection()` context manager (auto-commit, auto-close).
- Schema changes: add `ALTER TABLE` in `init_db()` wrapped in try/except for idempotency.

## File serving

The `/downloads-file/{download_id}/{filename:path}` route handles both single-file and directory downloads. When `file_path` is a directory (Douyin notes), it resolves `{filename}` inside it. Uses `mimetypes.guess_type()` for correct content-type headers.

## Deployment

- Docker multi-stage build: `python:3.13-slim`, installs `ffmpeg`, `xvfb`, `gosu`, Playwright Chromium.
- Xvfb is auto-started by `entrypoint.sh` before the app.
- Runs as non-root user `nicevid` (UID 1000).
- Env vars: `NICEVID_DATA_DIR`, `NICEVID_STORAGE_SECRET`, `NICEVID_RELOAD`, `DISPLAY=:99`.
- Volumes: `./downloads`, `./cookies`, `./data` mounted for persistence.
- Change `NICEVID_STORAGE_SECRET` in `docker-compose.yml` before production use.

## Key dependencies

| Package            | Purpose                                |
|--------------------|----------------------------------------|
| nicegui 3.9        | Web UI framework (Vue + Quasar)        |
| yt-dlp             | Video extraction & download            |
| playwright         | Browser automation for Douyin notes    |
| playwright-stealth | Anti-bot-detection for Playwright      |
| httpx              | HTTP client for image/video downloads  |
| fastapi            | HTTP routes (via NiceGUI)              |
| pydantic           | Data validation                        |
