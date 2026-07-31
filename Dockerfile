FROM ghcr.io/astral-sh/uv:0.8.15@sha256:a5727064a0de127bdb7c9d3c1383f3a9ac307d9f2d8a391edc7896c54289ced0 AS uv

FROM python:3.12.13-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md build-constraints.txt ./
COPY src ./src
RUN uv build --wheel --build-constraints build-constraints.txt --require-hashes --no-cache \
    && uv sync --frozen --no-dev --no-install-project --no-cache \
    && uv pip install --python /opt/venv/bin/python --no-deps --no-cache dist/*.whl

FROM python:3.12.13-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28 AS runtime

ARG SPANVOUCH_BUILD_GIT_COMMIT
ARG SPANVOUCH_BUILD_REPOSITORY_IDENTITY="github:naturaljam/SpanVouch"

LABEL org.opencontainers.image.title="SpanVouch" \
    org.opencontainers.image.revision="${SPANVOUCH_BUILD_GIT_COMMIT}"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPANVOUCH_BUILD_GIT_COMMIT="${SPANVOUCH_BUILD_GIT_COMMIT}" \
    SPANVOUCH_BUILD_REPOSITORY_IDENTITY="${SPANVOUCH_BUILD_REPOSITORY_IDENTITY}"

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/app app \
    && mkdir -p /app /data \
    && chown 10001:10001 /app /data
WORKDIR /app
COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv
COPY --from=builder --chown=10001:10001 /app/uv.lock /app/uv.lock

USER 10001:10001
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "spanvouch.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
