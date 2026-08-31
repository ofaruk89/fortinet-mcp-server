# FortiOS MCP Server Dockerfile
# Multi-stage build: dependencies resolved from uv.lock, slim runtime image.

# =============================================================================
# Stage 1: Builder — resolve dependencies from uv.lock into /app/.venv
# =============================================================================
FROM python:3.12-slim AS builder

# Pinned uv release (not :latest) so image builds stay reproducible
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Only the lock inputs — keeps this layer cached across source changes
COPY pyproject.toml uv.lock ./

# --no-install-project: pyproject declares packages = ["."], so installing the
# project itself would require the source tree here. server.py is run as a
# script, not imported as a package, so runtime deps alone are enough.
RUN uv sync --frozen --no-dev --no-install-project

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    PATH="/app/.venv/bin:$PATH"

# Image-level fallbacks. docker-compose overrides these from .env — see
# docker-compose.yaml. MCP_HOST must stay 0.0.0.0 inside the container or the
# published port is unreachable.
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY server.py fortios_client.py ./
COPY tools/ ./tools/

RUN useradd --create-home --shell /bin/bash fosmcp && \
    chown -R fosmcp:fosmcp /app

USER fosmcp

# Documentation only — the actually published port comes from MCP_PORT
EXPOSE 8000

# No HEALTHCHECK here on purpose: the listening port is configurable at run
# time, so the check is defined in docker-compose.yaml where ${MCP_PORT} is known.

CMD ["python", "server.py"]
