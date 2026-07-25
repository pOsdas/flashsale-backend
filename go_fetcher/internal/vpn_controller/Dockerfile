FROM python:3.12-slim

ARG XRAY_VERSION=26.7.11

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
    && curl -fsSL \
        "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-64.zip" \
        -o /tmp/xray.zip \
    && unzip /tmp/xray.zip xray -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/xray \
    && rm -f /tmp/xray.zip \
    && rm -rf /var/lib/apt/lists/*

COPY vpn_controller/requirements.txt ./vpn_controller/requirements.txt
RUN pip install --no-cache-dir -r ./vpn_controller/requirements.txt

COPY vpn_controller ./vpn_controller

RUN mkdir -p /app/state /app/state/runs

EXPOSE 8097

CMD ["gunicorn", "--bind", "0.0.0.0:8097", "--workers", "1", "--threads", "2", "--timeout", "180", "vpn_controller.manage:app"]
