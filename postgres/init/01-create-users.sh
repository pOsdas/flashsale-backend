#!/bin/sh

set -eu

psql \
  -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_user="$POSTGRES_APP_USER" \
  --set=app_password="$POSTGRES_APP_PASSWORD" \
  --set=exporter_user="$POSTGRES_EXPORTER_USER" \
  --set=exporter_password="$POSTGRES_EXPORTER_PASSWORD" <<'EOSQL'

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'app_user',
    :'app_password'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = :'app_user'
)
\gexec

SELECT format(
    'ALTER ROLE %I NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'app_user'
)
\gexec

SELECT format(
    'ALTER DATABASE %I OWNER TO %I',
    current_database(),
    :'app_user'
)
\gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE, CREATE
ON SCHEMA public
TO :"app_user";


SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L',
    :'exporter_user',
    :'exporter_password'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = :'exporter_user'
)
\gexec

SELECT format(
    'ALTER ROLE %I NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'exporter_user'
)
\gexec

GRANT pg_monitor TO :"exporter_user";

SELECT format(
    'GRANT CONNECT ON DATABASE %I TO %I',
    current_database(),
    :'exporter_user'
)
\gexec

EOSQL