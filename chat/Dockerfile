FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG CODEX_VERSION=0.159.3
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install --global @openai/codex@${CODEX_VERSION} \
    && pip install --no-cache-dir fastapi==0.141.1 uvicorn==0.34.2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home chat \
    && mkdir -p /sessions /auth \
    && chown chat:chat /sessions /auth
WORKDIR /app
COPY gateway.py .
USER chat
EXPOSE 8010
CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
