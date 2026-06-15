# syntax=docker/dockerfile:1

# --- Stage 1: build the React SPA -------------------------------------------------
FROM node:22-slim AS web
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci --no-audit --no-fund
COPY frontend ./frontend
# Vite emits into ../src/playstore_review_service/static/spa (see vite.config.ts),
# so the package path must exist in this stage.
COPY src ./src
RUN cd frontend && npm run build

# --- Stage 2: python runtime ------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DEV_INPROCESS_WORKER=0
WORKDIR /app

COPY pyproject.toml Readme.md ./
COPY src ./src
# Bring in the built SPA (from stage 1) before install so it ships inside the package.
COPY --from=web /build/src/playstore_review_service/static/spa ./src/playstore_review_service/static/spa
RUN pip install --no-cache-dir ".[postgres]"

# Drop privileges — run as a non-root user (limits blast radius of any RCE/traversal).
RUN useradd --system --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
# Default command runs the web server; the worker service overrides it (see compose).
CMD ["pmr-serve"]
