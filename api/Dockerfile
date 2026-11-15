# Runtime image for the API, plus a `test` target with dev dependencies and the tests.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /var/mail-capture \
 && chown app:app /var/mail-capture
COPY pyproject.toml ./
COPY app ./app
RUN pip install -e .

FROM base AS runtime
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
EXPOSE 8000
# starts as root only to copy secrets (see docker-entrypoint.sh), then runs as the app user
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

FROM base AS test
RUN pip install -e ".[dev]"
COPY tests ./tests
# runs as root so it can read the bind-mounted secret files directly
ENTRYPOINT ["python", "-m"]
CMD ["pytest"]
