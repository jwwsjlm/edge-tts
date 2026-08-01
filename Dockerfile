FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY setup.py setup.cfg README.md ./
COPY src ./src

RUN python -m venv /opt/edge-tts \
    && /opt/edge-tts/bin/python -m pip install --upgrade pip \
    && /opt/edge-tts/bin/python -m pip install .


FROM python:3.12-slim AS runtime

ENV PATH=/opt/edge-tts/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system edge-tts \
    && useradd --system --gid edge-tts --home-dir /app --no-create-home edge-tts \
    && mkdir -p /app /config \
    && chown edge-tts:edge-tts /app /config

COPY --from=builder /opt/edge-tts /opt/edge-tts

WORKDIR /app
USER edge-tts

EXPOSE 5050
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import pathlib,urllib.request,yaml; config=yaml.safe_load(pathlib.Path('/config/config.yaml').read_text(encoding='utf-8')); urllib.request.urlopen(f\"http://127.0.0.1:{config['port']}/health\", timeout=2).read()"]

ENTRYPOINT ["python", "-m", "edge_tts_server"]
CMD ["--config", "/config/config.yaml"]
