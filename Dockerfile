# Cloud Run image. Installs exactly what uv.lock pins (not the offline "build" group) and
# serves on $PORT. The processed data (data/*.duckdb, hex_grid.geojson) is in the repo.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies first, so code-only changes reuse this layer
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
ENV PATH="/app/.venv/bin:$PATH"
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}
