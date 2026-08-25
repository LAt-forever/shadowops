FROM mirror.gcr.io/library/python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

COPY --from=ghcr.io/astral-sh/uv:0.11.12@sha256:3a59a3cdd5f7c217faa36e32dbc7fddbb0412889c2a0a5229f6d790e5a019dd7 /uv /uvx /bin/
RUN groupadd --gid 10001 shadowops \
    && useradd --uid 10001 --gid shadowops --create-home --shell /usr/sbin/nologin shadowops
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev
RUN chown -R shadowops:shadowops /app
ENV PATH="/app/.venv/bin:$PATH"
USER shadowops
