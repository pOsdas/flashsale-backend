import os
import sys
import time
import django
import logging
import psycopg2
import subprocess
import urllib.parse
from dotenv import load_dotenv
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

load_dotenv()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app_project.settings")
django.setup()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

RETRY_COUNT = int(os.environ.get("DB_CREATE_RETRIES", 5))
RETRY_DELAY = int(os.environ.get("DB_CREATE_RETRY_DELAY", 2))


def get_database_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    dj_settings = os.environ.get("DJANGO_SETTINGS_MODULE")
    if not dj_settings:
        raise RuntimeError(
            "DATABASE_URL не установлена и DJANGO_SETTINGS_MODULE не установлен;"
            " невозможно определить подключение к бд."
        )

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", dj_settings)
    import django
    django.setup()
    from django.conf import settings
    db = settings.DATABASES.get("default")
    if not db:
        raise RuntimeError("DATABASES['default'] не найдено Django settings.")
    engine = db.get("ENGINE", "")
    if "postgresql" not in engine:
        raise RuntimeError("Ядро DATABASE не является PostgreSQL.")
    user = db.get("USER") or ""
    password = db.get("PASSWORD") or ""
    host = db.get("HOST") or "localhost"
    port = db.get("PORT") or "5432"
    name = db.get("NAME") or ""
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def parse_url(url):
    parsed = urllib.parse.urlparse(url)
    username = urllib.parse.unquote(parsed.username) if parsed.username else None
    password = urllib.parse.unquote(parsed.password) if parsed.password else None
    db_name = parsed.path.lstrip("/")
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    return {
        "user": username,
        "password": password,
        "host": host,
        "port": port,
        "db_name": db_name,
    }


def ensure_db_exists(db_url_info):
    user = db_url_info["user"]
    password = db_url_info["password"]
    host = db_url_info["host"]
    port = db_url_info["port"]
    db_name = db_url_info["db_name"]

    # Подключаемся к maintenance DB
    maintenance_db = os.environ.get("POSTGRES_MAINTENANCE_DB", "postgres")

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            conn = psycopg2.connect(
                dbname=maintenance_db,
                user=user,
                password=password,
                host=host,
                port=port,
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (db_name,))
            exists = cur.fetchone()
            if exists:
                logger.info(f"[ensure_db] Database '{db_name}' уже существует.")
                cur.close()
                conn.close()
                return False  # создано не было
            # разрешено ли автосоздание
            allow = os.environ.get("ALLOW_DB_CREATE", "0")
            if allow != "1":
                raise RuntimeError(
                    f"Database '{db_name}' не существует и ALLOW_DB_CREATE!=1. "
                    "Enable ALLOW_DB_CREATE=1 for local/CI only."
                )
            logger.info(f"[ensure_db] Создание базы данных '{db_name}'...")
            cur.execute(f'CREATE DATABASE "{db_name}";')
            logger.info(f"[ensure_db] Database '{db_name}' создана.")
            cur.close()
            conn.close()
            return True  # база создана
        except psycopg2.OperationalError as exc:
            # возможно БД ещё не поднята (в docker-compose) — ждём и повторяем
            logger.warning(f"[ensure_db] OperationalError (попытка {attempt}/{RETRY_COUNT}): {exc}")
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
                continue
            raise
        except psycopg2.errors.DuplicateDatabase:
            # кто-то создал параллельно
            logger.warning(f"[ensure_db] Database '{db_name}' уже существует в другом процессе.")
            return False
        except Exception:
            raise


def run_migrations():
    logger.info("[ensure_db] Running Django migrations...")
    subprocess.run([sys.executable, "manage.py", "migrate", "app", "--noinput"], check=True)
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
    logger.info("[ensure_db] Migrations finished.")


def main():
    db_url = get_database_url()
    info = parse_url(db_url)
    created = ensure_db_exists(info)
    if created:
        cmd = os.environ.get("POST_CREATE_CMD")
        if cmd:
            logger.info(f"[ensure_db] Running post-create command: {cmd}")
            subprocess.run(cmd, shell=True, check=True)
    # Всегда запускаем миграции (идемпотентно)
    run_migrations()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.info(f"[ensure_db] Error: {e}")
        sys.exit(1)