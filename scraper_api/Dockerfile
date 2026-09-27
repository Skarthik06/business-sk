# Business-SK Scraper API — FastAPI + HTTPX + Playwright (Chromium). Runs as a non-root user.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt \
    && playwright install --with-deps chromium \
    && useradd -m -u 10001 scraper \
    && mkdir -p /data/cache && chown -R scraper:scraper /data \
    && chmod -R a+rx /ms-playwright

COPY . .
USER scraper
VOLUME ["/data"]
EXPOSE 8200
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
