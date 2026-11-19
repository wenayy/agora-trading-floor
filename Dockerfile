# --- build the dashboard
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- runtime
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 AGORA_WEB_DIST=/app/web
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
COPY --from=web /web/dist ./web
RUN useradd --create-home app && mkdir -p /data && chown -R app /app /data
USER app
ENV AGORA_DB_PATH=/data/floor.db
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["agora", "serve", "--host", "0.0.0.0", "--port", "8000"]
