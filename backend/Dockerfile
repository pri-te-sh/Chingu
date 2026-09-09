# Pixel brain / worker image (multi-arch: amd64 + arm64). Same image, different command.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_SYSTEM_PYTHON=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --no-hashes --format requirements-txt > requirements.txt && uv pip install --system -r requirements.txt
COPY pixel ./pixel
COPY alembic.ini ./
COPY alembic ./alembic
ENV PIXEL_MODELS_DIR=/models PIXEL_DATA_DIR=/data
VOLUME ["/models", "/data"]
EXPOSE 8765
CMD ["uvicorn", "pixel.server:app", "--host", "0.0.0.0", "--port", "8765", "--log-level", "warning"]
