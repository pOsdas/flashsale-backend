import logging.config
import os
from pathlib import Path
from dotenv import load_dotenv

import dj_database_url

import app.core.logging_filters
from app.core.config import get_settings


# Base project dir
BASE_DIR = Path(__file__).resolve().parent

APP_ENV = os.getenv(
    "APP_ENV",
    "local",
).strip().lower()

if APP_ENV == "local":
    load_dotenv(
        dotenv_path=BASE_DIR.parent / ".env.local",
        override=False,
    )

s = get_settings()

# Основные настройки
SECRET_KEY = s.secret_key
DEBUG = s.debug
ENABLE_HTTPS_REDIRECT = s.enable_https_redirect
ALLOWED_HOSTS = s.allowed_hosts

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Internationalization
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Amsterdam"
USE_I18N = True
USE_TZ = True

# Applications
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_prometheus",

    # for webhooks
    "rest_framework",
    "drf_spectacular",

    "app.api.v1.common.apps.V1CommonConfig",
    "app.api.v1.catalog.apps.V1CatalogConfig",
    "app.api.v1.orders.apps.V1OrdersConfig",
    "app.api.v1.payments.apps.V1PaymentsConfig",
    "app.api.v1.monitoring.apps.V1MonitoringConfig",
    "app.api.v1.notifications.apps.V1NotificationsConfig",
    "app.api.v1.load_testing.apps.V1LoadTestingConfig",
]

# Middleware
MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "app.core.middleware.request_id.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = "app_project.urls"

TEMPLATES = [
    {
        "BACKEND": (
            "django.template.backends.django.DjangoTemplates"
        ),
        "DIRS": [
            BASE_DIR.parent / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                (
                    "django.template.context_processors."
                    "debug"
                ),
                (
                    "django.template.context_processors."
                    "request"
                ),
                (
                    "django.contrib.auth.context_processors."
                    "auth"
                ),
                (
                    "django.contrib.messages.context_processors."
                    "messages"
                ),
            ],
        },
    },
]

ASGI_APPLICATION = "app_project.asgi.application"
WSGI_APPLICATION = "app_project.wsgi.application"

# Database
DATABASES = {
    "default": dj_database_url.parse(
        s.database_url,
        conn_max_age=s.db_conn_max_age,
        ssl_require=False,
    )
}

DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["prepare_threshold"] = None

# Static
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR.parent / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": (
            "django.core.files.storage."
            "FileSystemStorage"
        ),
    },
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage."
            "CompressedManifestStaticFilesStorage"
        ),
    },
}

# Redis / Celery
REDIS_URL = str(s.redis_url)

CELERY_BROKER_URL = str(s.celery_broker_url)

if s.celery_result_backend is not None:
    CELERY_RESULT_BACKEND = str(
        s.celery_result_backend
    )

CELERY_ACCEPT_CONTENT = [
    "json",
]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Go Fetcher
MONITORING_FETCHER_MODE = os.getenv(
    "MONITORING_FETCHER_MODE",
    "fake",
)

GO_FETCHER_BASE_URL = os.getenv(
    "GO_FETCHER_BASE_URL",
    "http://localhost:8090",
)
GO_FETCHER_PARSER_HEALTH_ENDPOINT = os.getenv(
    "GO_FETCHER_PARSER_HEALTH_ENDPOINT",
    "/api/v1/parser/health/",
)
GO_FETCHER_PRODUCT_ENDPOINT = os.getenv(
    "GO_FETCHER_PRODUCT_ENDPOINT",
    "/api/v1/fetch/product",
)
GO_FETCHER_API_KEY = os.getenv(
    "GO_FETCHER_API_KEY",
    "",
)
GO_FETCHER_TIMEOUT_SECONDS = int(
    os.getenv(
        "GO_FETCHER_TIMEOUT_SECONDS",
        "60",
    )
)
GO_FETCHER_PARSER_HEALTH_TIMEOUT_SECONDS = int(
    os.getenv(
        "GO_FETCHER_PARSER_HEALTH_TIMEOUT_SECONDS",
        "270",
    )
)

# Load testing
LOAD_TESTING_ENABLED = (
    os.getenv("LOAD_TESTING_ENABLED", "False")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)
LOAD_TESTING_API_KEY = os.getenv(
    "LOAD_TESTING_API_KEY",
    "",
).strip()
LOAD_TESTING_USER_HEADER = os.getenv(
    "LOAD_TESTING_USER_HEADER",
    "HTTP_X_LOAD_TEST_USER_ID",
).strip()

# Notifications
NOTIF_TELEGRAM_BOT_TOKEN = os.getenv(
    "NOTIF_TELEGRAM_BOT_TOKEN",
    "",
)

NOTIF_TELEGRAM_BOT_USERNAME = os.getenv(
    "NOTIF_TELEGRAM_BOT_USERNAME",
    "",
)
NOTIF_TELEGRAM_API_BASE_URL = os.getenv(
    "NOTIF_TELEGRAM_API_BASE_URL",
    "https://api.telegram.org",
).rstrip("/")

NOTIF_TELEGRAM_CONNECT_TOKEN_MAX_AGE_SECONDS = int(
    os.getenv(
        "NOTIF_TELEGRAM_CONNECT_TOKEN_MAX_AGE_SECONDS",
        "900",
    )
)
NOTIF_TELEGRAM_CONNECT_SIGNING_SALT = os.getenv(
    "NOTIF_TELEGRAM_CONNECT_SIGNING_SALT",
    "",
)
NOTIF_TELEGRAM_REPLY_RATE_LIMIT_LIMIT = int(
    os.getenv(
        "NOTIF_TELEGRAM_REPLY_RATE_LIMIT_LIMIT",
        "1",
    )
)
NOTIF_TELEGRAM_REPLY_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv(
        "NOTIF_TELEGRAM_REPLY_RATE_LIMIT_WINDOW_SECONDS",
        "10",
    )
)
NOTIF_TELEGRAM_PREVIEW_RATE_LIMIT_LIMIT = int(
    os.getenv(
        "NOTIF_TELEGRAM_PREVIEW_RATE_LIMIT_LIMIT",
        "1",
    )
)
NOTIF_TELEGRAM_PREVIEW_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv(
        "NOTIF_TELEGRAM_PREVIEW_RATE_LIMIT_WINDOW_SECONDS",
        "10",
    )
)
NOTIF_TELEGRAM_CHECK_NOW_RATE_LIMIT_LIMIT = int(
    os.getenv(
        "NOTIF_TELEGRAM_CHECK_NOW_RATE_LIMIT_LIMIT",
        "3",
    )
)

NOTIF_TELEGRAM_CHECK_NOW_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv(
        "NOTIF_TELEGRAM_CHECK_NOW_RATE_LIMIT_WINDOW_SECONDS",
        "60",
    )
)
NOTIF_TELEGRAM_CALLBACK_RATE_LIMIT_LIMIT = int(
    os.getenv(
        "NOTIF_TELEGRAM_CALLBACK_RATE_LIMIT_LIMIT",
        "1",
    )
)
NOTIF_TELEGRAM_CALLBACK_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv(
        "NOTIF_TELEGRAM_CALLBACK_RATE_LIMIT_WINDOW_SECONDS",
        "10",
    )
)
NOTIF_TELEGRAM_DROP_PENDING_UPDATES_ON_START = (
    os.getenv(
        "NOTIF_TELEGRAM_DROP_PENDING_UPDATES_ON_START",
        "False",
    )
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

NOTIFICATION_CONSUMER_METRICS_PORT = int(
    os.getenv(
        "NOTIFICATION_CONSUMER_METRICS_PORT",
        "8011",
    )
)

NOTIF_TELEGRAM_PENDING_PRODUCT_TTL_SECONDS = int(
    os.getenv(
        "NOTIF_TELEGRAM_PENDING_PRODUCT_TTL_SECONDS",
        "600",
    )
)
NOTIF_TELEGRAM_PENDING_PRODUCT_LOCK_SECONDS = int(
    os.getenv(
        "NOTIF_TELEGRAM_PENDING_PRODUCT_LOCK_SECONDS",
        "30"
    )
)
NOTIF_TELEGRAM_METRICS_PORT = int(
    os.getenv(
        "NOTIF_TELEGRAM_METRICS_PORT",
        "8013",
    )
)

NOTIFICATION_RABBITMQ_QUEUE = os.getenv(
    "NOTIFICATION_RABBITMQ_QUEUE",
    "flashsale.notifications",
)
NOTIFICATION_RABBITMQ_DLQ = os.getenv(
    "NOTIFICATION_RABBITMQ_DLQ",
    "flashsale.notifications.dlq",
)
NOTIFICATION_RABBITMQ_DLX = os.getenv(
    "NOTIFICATION_RABBITMQ_DLX",
    "flashsale.notifications.dlx",
)
NOTIFICATION_RABBITMQ_PREFETCH_COUNT = int(
    os.getenv(
        "NOTIFICATION_RABBITMQ_PREFETCH_COUNT",
        "10",
    )
)

NOTIFICATION_RABBITMQ_ROUTING_KEYS = [
    item.strip()
    for item in os.getenv(
        "NOTIFICATION_RABBITMQ_ROUTING_KEYS",
        "alert.created",
    ).split(",")
    if item.strip()
]

# RabbitMQ
OUTBOX_DISPATCH_MODE = os.getenv(
    "OUTBOX_DISPATCH_MODE",
    "local",
)
RABBITMQ_URL = os.getenv(
    "RABBITMQ_URL",
    "amqp://guest:guest@localhost:5672/",
)
RABBITMQ_EXCHANGE = os.getenv(
    "RABBITMQ_EXCHANGE",
    "flashsale.events",
)

# Security
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

if ENABLE_HTTPS_REDIRECT:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # if Nginx/Ingress
    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

# Passwords
PASSWORD_HASHERS = [
    (
        "django.contrib.auth.hashers."
        "Argon2PasswordHasher"
    ),
    (
        "django.contrib.auth.hashers."
        "PBKDF2PasswordHasher"
    ),
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        )
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        )
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        )
    },
]

# Django REST Framework
_rest_authentication_classes = [
    (
        "rest_framework.authentication."
        "SessionAuthentication"
    ),
]

if LOAD_TESTING_ENABLED:
    _rest_authentication_classes.append(
        "app.api.v1.load_testing.authentication."
        "LoadTestHeaderAuthentication"
    )

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": (
        "drf_spectacular.openapi.AutoSchema"
    ),
    "DEFAULT_AUTHENTICATION_CLASSES": (
        _rest_authentication_classes
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "EXCEPTION_HANDLER": (
        "app.api.exceptions.api_exception_handler"
    ),
}


SPECTACULAR_SETTINGS = {
    "TITLE": "Flashsale Backend API",
    "DESCRIPTION": (
        "API documentation for flashsale-backend"
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,

    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayOperationId": True,
    },
}

# Webhooks / Go fetcher
STRIPE_WEBHOOK_SECRET = getattr(
    s,
    "stripe_webhook_secret",
    "dev_stripe_webhook_secret",
)
FETCHER_QUEUE_KEY = getattr(
    s,
    "fetcher_queue_key",
    "fetcher:queue",
)
FETCHER_RESULT_PREFIX = getattr(
    s,
    "fetcher_result_prefix",
    "fetcher:result:",
)

# Logging
LOG_FORMAT = os.getenv(
    "LOG_FORMAT",
    "colored",
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "colored": {
            "()": "colorlog.ColoredFormatter",
            "format": (
                "%(log_color)s%(asctime)s "
                "[%(levelname)s] "
                "%(name)s: %(message)s"
            ),
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
            "()": (
                "pythonjsonlogger.jsonlogger."
                "JsonFormatter"
            ),
            "fmt": (
                "%(asctime)s "
                "%(levelname)s "
                "%(name)s "
                "%(module)s "
                "%(funcName)s "
                "%(lineno)d "
                "%(message)s "
                "%(service)s "
                "%(request_id)s "
                "%(method)s "
                "%(path)s "
                "%(status_code)s "
                "%(duration_ms)s "
                "%(event_id)s "
                "%(topic)s "
                "%(attempts)s "
                "%(error)s"
            ),
        },
    },
    "filters": {
        "request_id": {
            "()": (
                "app.core.logging_filters."
                "RequestIdLoggingFilter"
            )
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": (
                "json"
                if LOG_FORMAT == "json"
                else "colored"
            ),
            "level": "DEBUG",
            "filters": [
                "request_id",
            ],
        },
    },
    "root": {
        "handlers": [
            "console",
        ],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": [
                "console",
            ],
            "level": "INFO",
            "propagate": False,
        },
        "outbox": {
            "handlers": [
                "console",
            ],
            "level": "INFO",
            "propagate": False,
        },
        "httpx": {
            "handlers": [
                "console",
            ],
            "level": "WARNING",
            "propagate": False,
        },
        "httpcore": {
            "handlers": [
                "console",
            ],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

logging.config.dictConfig(LOGGING)
