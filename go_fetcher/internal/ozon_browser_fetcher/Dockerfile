FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV LANG=ru_RU.UTF-8
ENV LC_ALL=ru_RU.UTF-8
ENV TZ=Europe/Moscow

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        locales \
        openbox \
        tzdata \
        xvfb \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-noto-core \
        fonts-noto-cjk \
    && echo "ru_RU.UTF-8 UTF-8" > /etc/locale.gen \
    && locale-gen \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

COPY ozon_browser_fetcher/requirements.txt ./ozon_browser_fetcher/requirements.txt
RUN pip install --no-cache-dir -r ./ozon_browser_fetcher/requirements.txt

COPY browser_runtime ./browser_runtime
COPY ozon_browser_fetcher ./ozon_browser_fetcher

RUN mkdir -p /tmp/browser-runtime /data/browser-profile /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix

EXPOSE 8095

CMD ["gunicorn", "--bind", "0.0.0.0:8095", "--workers", "1", "--threads", "8", "--timeout", "240", "ozon_browser_fetcher.manage:app"]
