# Runtime image for the sync worker, plus a `test` target with dev dependencies and the tests.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
RUN useradd --create-home --uid 10002 syncer
COPY pyproject.toml ./
COPY sync ./sync
COPY sql ./sql
COPY tables ./tables
RUN pip install -e .

FROM base AS runtime
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# starts as root only to copy secrets (see docker-entrypoint.sh), then runs as the syncer user
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "sync"]

FROM base AS test
RUN pip install -e ".[dev]"
COPY tests ./tests
# runs as root so it can read the bind-mounted secret files directly
ENTRYPOINT ["python", "-m"]
CMD ["pytest"]
