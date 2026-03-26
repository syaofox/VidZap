FROM python:3.13-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
# placeholder so uv sync can inspect the project
RUN mkdir -p src/core src/pages src/components && \
    touch src/__init__.py src/core/__init__.py src/pages/__init__.py src/components/__init__.py && \
    uv sync --frozen --no-dev

FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg gosu xvfb && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local/bin/uv /root/.local/bin/uv
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"

WORKDIR /app

COPY src/ src/
COPY pyproject.toml ./

# Install Playwright Chromium browser and its system dependencies
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright
RUN playwright install chromium && \
    playwright install-deps chromium

RUN groupadd -g 1000 nicevid && \
    useradd -u 1000 -g nicevid -m nicevid && \
    mkdir -p downloads cookies data && \
    chown -R nicevid:nicevid /app

COPY entrypoint.sh /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "src/main.py"]
