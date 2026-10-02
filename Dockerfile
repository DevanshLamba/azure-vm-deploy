# syntax=docker/dockerfile:1
# CloudTasks app image: slim Python, non-root user, healthcheck.
# `docker build --target test .` runs the API tests (CI-style); the default target is the runtime image.

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- test stage: same dependencies + pytest, runs the suite during the build --------------
FROM base AS test
COPY requirements-test.txt .
RUN pip install -r requirements-test.txt
COPY pytest.ini .
COPY app ./app
COPY tests ./tests
RUN python -m pytest -q -p no:cacheprovider

# ---- runtime stage --------------------------------------------------------------------------
FROM base AS runtime
ARG APP_VERSION=1.0.0
ENV APP_VERSION=${APP_VERSION} \
    DB_PATH=/data/cloudtasks.db \
    DISK_PATH=/data
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data
COPY --chown=root:root app ./app
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
# One worker on purpose: the rate limiter keeps its counters in process memory.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
