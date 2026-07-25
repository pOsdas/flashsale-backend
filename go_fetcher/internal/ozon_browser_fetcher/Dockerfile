FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY ozon_browser_fetcher/requirements.txt ./ozon_browser_fetcher/requirements.txt

RUN pip install --no-cache-dir -r ./ozon_browser_fetcher/requirements.txt

RUN playwright install --with-deps chromium

COPY ozon_browser_fetcher ./ozon_browser_fetcher

EXPOSE 8095

CMD ["gunicorn", "--bind", "0.0.0.0:8095", "--workers", "1", "--threads", "1", "--timeout", "120", "ozon_browser_fetcher.manage:app"]
