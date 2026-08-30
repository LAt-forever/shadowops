FROM mirror.gcr.io/library/python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

COPY --from=ghcr.io/astral-sh/uv:0.11.12@sha256:3a59a3cdd5f7c217faa36e32dbc7fddbb0412889c2a0a5229f6d790e5a019dd7 /uv /uvx /bin/
RUN groupadd --gid 10002 runner \
    && useradd --uid 10002 --gid runner --no-create-home --shell /usr/sbin/nologin runner
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && rm -rf /root/.cache
COPY runner/src ./src
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app/src"
USER 10002:10002
ENTRYPOINT ["python", "-m", "shadowops_runner.main"]
