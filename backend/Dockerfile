# syntax=docker/dockerfile:1
# JobPilot backend: FastAPI + Alembic + headless Chromium for the Browser Assistant.
#
#   docker build -t jobpilot-backend ./backend
#
# No secrets or .env files are copied into the image (see .dockerignore); all
# configuration comes from the environment at run time.
#
# Chromium must match the pinned Playwright (requirements.txt: playwright==1.56.0). It is
# taken from Microsoft's official, version-tagged Playwright image -- only the headless
# shell, not the image's Firefox/WebKit -- so the build needs no browser download. When
# Playwright is upgraded, update PLAYWRIGHT_IMAGE and CHROMIUM_REVISION together
# (`playwright install --dry-run chromium-headless-shell` prints the revision).

ARG BASE_IMAGE=ubuntu:24.04
ARG PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright/python:v1.56.0-noble

FROM ${PLAYWRIGHT_IMAGE} AS browser

# ---- base: Ubuntu 24.04's Python 3.12 -----------------------------------------------------
FROM ${BASE_IMAGE} AS base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---- build: the pinned Python dependencies in a virtualenv --------------------------------
FROM base AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python3 -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

# ---- runtime -----------------------------------------------------------------------------------
FROM base AS runtime
ARG CHROMIUM_REVISION=1194
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    # A container has no display: the Browser Assistant runs Chromium headless and shows
    # the user a live screenshot instead of a window.
    AUTOMATION_BROWSER_HEADLESS=true \
    STORAGE_PATH=/app/storage/resumes

COPY --from=build /opt/venv /opt/venv
# Chromium's system libraries (Ubuntu packages, via Playwright's own list), then the browser.
RUN playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/* /tmp/*
COPY --from=browser /ms-playwright/chromium_headless_shell-${CHROMIUM_REVISION} \
    /opt/ms-playwright/chromium_headless_shell-${CHROMIUM_REVISION}

# Unprivileged user; the only writable application path is the storage directory.
RUN groupadd --system --gid 10001 jobpilot \
    && useradd --system --uid 10001 --gid jobpilot --home-dir /app --shell /usr/sbin/nologin jobpilot \
    && mkdir -p /app/storage/resumes \
    && chown -R jobpilot:jobpilot /app/storage

WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

USER jobpilot
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
