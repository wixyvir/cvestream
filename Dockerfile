FROM ghcr.io/astral-sh/uv:0.13.8-python3.14-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_NO_DEV=1
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

FROM builder AS dev
ENV UV_NO_DEV=0 PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
CMD ["watchfiles", "cvestream run", "src"]

FROM python:3.15-slim-trixie AS runtime
RUN groupadd --system --gid 999 cvestream \
 && useradd --system --uid 999 --gid 999 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin cvestream \
 && mkdir /data && chown 999:999 /data
# only the venv and src: the project is installed in editable mode (a .pth to /app/src)
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CVESTREAM_DATA_DIR=/data
VOLUME /data
EXPOSE 8080 9090
USER 999:999
WORKDIR /app
ENTRYPOINT ["cvestream"]
# /healthz on the private listener; the start period covers the migration and
# the first config load. A different private port needs `healthcheck --port`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["cvestream", "healthcheck"]
CMD ["run"]
