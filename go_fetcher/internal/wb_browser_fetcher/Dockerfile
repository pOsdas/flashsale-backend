FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY wb_browser_fetcher/requirements.txt ./wb_browser_fetcher/requirements.txt
RUN pip install --no-cache-dir -r ./wb_browser_fetcher/requirements.txt
RUN playwright install --with-deps chromium

COPY wb_browser_fetcher ./wb_browser_fetcher

EXPOSE 8096

CMD ["gunicorn", "--bind", "0.0.0.0:8096", "--workers", "1", "--worker-class", "sync", "--timeout", "120", "wb_browser_fetcher.manage:app"]
