import os
import logging.config
from pathlib import Path

import dj_database_url

from app.core.config import get_settings

# Base project dir
BASE_DIR = Path(__file__).resolve().parent

s = get_settings()

# Основные настройки
SECRET_KEY = s.secret_key
DEBUG = s.debug
ALLOWED_HOSTS = s.allowed_hosts

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Internationalization
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Amsterdam"
USE_I18N = True
USE_TZ = True

# Applications
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # for webhooks
    'rest_framework',

    "app.api.v1.catalog.apps.V1CatalogConfig",
    "app.api.v1.orders.apps.V1OrdersConfig",
    "app.api.v1.payments.apps.V1PaymentsConfig",
]
# Middleware
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'app_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # 'DIRS': [BASE_DIR / 'templates'],
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

ASGI_APPLICATION = 'app_project.asgi.application'
WSGI_APPLICATION = 'app_project.wsgi.application'

# Database
DATABASES = {
    'default': dj_database_url.parse(
        s.database_url,
        conn_max_age=60,
        ssl_require=False,
    )
}

# Static
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static'

# Redis / Celery
REDIS_URL = str(s.redis_url)

CELERY_BROKER_URL = str(s.celery_broker_url)
if s.celery_result_backend is not None:
    CELERY_RESULT_BACKEND = str(s.celery_result_backend)

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Security
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # if Nginx/Ingress
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    SECURE_SSL_REDIRECT = False

# Passwords
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher'
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# DRF for webhook
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
}

# Webhooks / Go fetcher
STRIPE_WEBHOOK_SECRET = getattr(s, "stripe_webhook_secret", "dev_stripe_webhook_secret")
FETCHER_QUEUE_KEY = getattr(s, "fetcher_queue_key", "fetcher:queue")
FETCHER_RESULT_PREFIX = getattr(s, "fetcher_result_prefix", "fetcher:result:")

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "colored": {
            "()": "colorlog.ColoredFormatter",
            "format": "%(log_color)s%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "log_colors": {
                "DEBUG": "cyan",
                "INFO": "white",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        },
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "fmt": "%(levelname)s %(name)s %(message)s %(asctime)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "colored" if DEBUG else "json",
            "level": "DEBUG",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

logging.config.dictConfig(LOGGING)