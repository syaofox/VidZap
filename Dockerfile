# syntax=docker/dockerfile:1
FROM python:3.13-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
# placeholder so uv sync can inspect the project
RUN mkdir -p src/core src/pages && \
    touch src/__init__.py src/core/__init__.py src/pages/__init__.py

# Cache uv package downloads so dependency changes don't re-download from PyPI
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.13-slim

# Cache apt archives so system dep changes don't re-download
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg gosu xvfb nodejs

COPY --from=builder /root/.local/bin/uv /usr/local/bin/uv
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:/usr/local/bin:$PATH"

WORKDIR /app

# User/group setup (rarely changes — placed early for layer caching)
RUN groupadd -g 1000 nicevid && \
    useradd -u 1000 -g nicevid -m nicevid

# Project metadata and entrypoint (rarely changes)
COPY pyproject.toml ./
COPY entrypoint.sh /entrypoint.sh

# Install Playwright Chromium and its system dependencies
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright
RUN playwright install chromium && \
    playwright install-deps chromium

# Data directories (only re-runs when their structure changes)
RUN mkdir -p downloads data && \
    chown -R nicevid:nicevid downloads data

# Application source (changes most frequently — comes last for caching)
# --chown 保证源码归 nicevid 所有，避免源文件权限位导致运行用户不可读
COPY --chown=nicevid:nicevid src/ src/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/', timeout=5)"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "src/main.py"]
