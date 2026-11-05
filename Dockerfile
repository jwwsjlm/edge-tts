FROM node:24-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY setup.py setup.cfg README.md ./
COPY src ./src
COPY --from=frontend /build/src/edge_tts_server/static ./src/edge_tts_server/static

RUN python -m venv /opt/edge-tts \
    && /opt/edge-tts/bin/python -m pip install --upgrade pip \
    && /opt/edge-tts/bin/python -m pip install .


FROM python:3.14-slim AS runtime

ENV PATH=/opt/edge-tts/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 edge-tts \
    && useradd --uid 10001 --gid edge-tts --home-dir /app \
        --no-create-home --shell /usr/sbin/nologin edge-tts \
    && mkdir -p /app /config/data \
    && chown edge-tts:edge-tts /app /config /config/data

COPY --from=builder /opt/edge-tts /opt/edge-tts

WORKDIR /app
USER edge-tts

EXPOSE 5050
VOLUME /config/data
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import pathlib,urllib.request,yaml; config=yaml.safe_load(pathlib.Path('/config/config.yaml').read_text(encoding='utf-8')); urllib.request.urlopen(f\"http://127.0.0.1:{config['port']}/health\", timeout=2).read()"]

ENTRYPOINT ["python", "-m", "edge_tts_server"]
CMD ["--config", "/config/config.yaml"]
