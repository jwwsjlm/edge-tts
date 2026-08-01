FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY setup.py setup.cfg README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && groupadd --system edge-tts \
    && useradd --system --gid edge-tts --home-dir /app --no-create-home edge-tts \
    && mkdir -p /config \
    && chown edge-tts:edge-tts /config

USER edge-tts

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/health', timeout=2).read()"]

ENTRYPOINT ["python", "-m", "edge_tts_server"]
CMD ["--config", "/config/config.yaml"]
