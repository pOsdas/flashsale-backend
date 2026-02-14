from pathlib import Path
import logging
import logging.config
import environ
from app.core.config import settings as pydantic_settings

env = environ.Env()

# Корневая директория проекта
BASE_DIR = Path(__file__).resolve().parent

# Основные настройки
DOCKER = env.bool("DOCKER", False)
SECRET_KEY = pydantic_settings.secret_key
DEBUG = pydantic_settings.debug
ALLOWED_HOSTS = env.str("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

# Настройка базы данных
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': env.str('POSTGRES_DB'),
        'USER': env.str('POSTGRES_USER'),
        'PASSWORD': env.str('POSTGRES_PASSWORD'),
        'HOST': env.str('POSTGRES_HOST') if DOCKER else 'localhost',
        'PORT': env.str('POSTGRES_PORT')
    }
}

# DATABASES["default"]["ENGINE"] = "django.db.backends.postgresql_async"
DATABASES["default"]["ENGINE"] = "django.db.backends.postgresql"

# Настройка Redis
REDIS_HOST = env.str("REDIS_HOST", "localhost") if DOCKER else '127.0.0.1'
REDIS_PORT = env.str("REDIS_PORT", "6379")  # if DOCKER else '127.0.0.1:6379'
REDIS_DB = env.str("REDIS_DB", "1")
REDIS_DECODE_RESPONSES = True

# TTL для redis

# Ключи redis

# Настройка Celery
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL")  # f"redis://{_REDIS_HOST}:{_REDIS_PORT}/{REDIS_DB}"
CELERY_RESULT_BACKEND = env.str("CELERY_BROKER_URL")  # f"redis://{_REDIS_HOST}:{_REDIS_PORT}/{REDIS_DB}"
CELERY_BEAT_SCHEDULE = {}
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Установленные приложения
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework.authtoken',
    'drf_spectacular',
    'rest_framework',
    'app.catalog',
    "app.common",
    "app.fetcher",
    "app.orders",
    "app.payments",
    'axes',
]

MIDDLEWARE = [
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.csp.middleware.CSPMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.axes.middleware.AxesMiddleware',
]

CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'self'",)
CSP_STYLE_SRC = ("'self'",)
CSP_IMG_SRC = ("'self'", "data:")
CSP_FONT_SRC = ("'self'", "https://fonts.googleapis.com")

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.Argon2PasswordHasher',
]

AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", 5)
AXES_COOLOFF_TIME = env.int("AXES_COOLOFF_TIME", 60*15)

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
else:
    SECURE_SSL_REDIRECT = False

ROOT_URLCONF = 'app_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
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

# Статические файлы
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static'


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "app_project.wsgi.application"
ASGI_APPLICATION = "app_project.asgi.application"

# ===== DATABASE =====
# app_settings.database_url должен быть вида:
# postgresql://user:pass@host:5432/dbname
DATABASES = {
    "default": dj_database_url.parse(
        app_settings.database_url,
        conn_max_age=60,  # держим коннекты (полезно под нагрузкой)
        ssl_require=False,
    )
}

# ===== INTERNATIONALIZATION =====
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Berlin"  # можешь сменить на свой, но лучше IANA
USE_I18N = True
USE_TZ = True

# ===== STATIC =====
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ===== DRF (для вебхуков, админских ручек и т.п.) =====
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
}

# ===== WEBHOOKS =====
STRIPE_WEBHOOK_SECRET = app_settings.stripe_webhook_secret

# ===== GO FETCHER PROTOCOL =====
FETCHER_QUEUE_KEY = app_settings.fetcher_queue_key
FETCHER_RESULT_PREFIX = app_settings.fetcher_result_prefix

# (Опционально) Настройки Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.TokenAuthentication"
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        "rest_framework.permissions.IsAuthenticated",
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "500/day",
    },
}

AUTH_USER_MODEL = "app.User"

# Русский язык
LANGUAGE_CODE = 'ru'
USE_I18N = True
USE_L10N = True

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
    {
        "NAME": "app.api.core.CustomPasswordValidator",
    },
]

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