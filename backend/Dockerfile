FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm poppler-utils fonts-noto-core \
    && npm install --global @openai/codex@0.165.0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
RUN groupadd --gid 10001 boq-audit \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/boq-audit --shell /usr/sbin/nologin boq-audit \
    && mkdir -p /data /home/boq-audit/.codex \
    && chown -R 10001:10001 /home/boq-audit /data
ENV HOME=/home/boq-audit CODEX_HOME=/home/boq-audit/.codex
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
