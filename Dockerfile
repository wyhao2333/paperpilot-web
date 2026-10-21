FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_HOST=0.0.0.0 APP_PORT=8765 APP_DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 paperpilot \
    && useradd --uid 10001 --gid 10001 --create-home paperpilot \
    && mkdir /app/data && chown paperpilot:paperpilot /app/data
COPY paperpilot/ ./paperpilot/
COPY examples/ ./examples/
COPY --from=frontend /build/dist ./frontend/dist
USER paperpilot
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=3)"
CMD ["python", "-m", "paperpilot"]
