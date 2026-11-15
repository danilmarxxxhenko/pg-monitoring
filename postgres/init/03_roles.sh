#!/usr/bin/env bash
# Creates least-privilege roles for services. Passwords come from the
# container environment (populated from .env by docker compose).
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$APP_DB_USER" -v app_pass="$APP_DB_PASSWORD" \
     -v gen_user="$GENERATOR_DB_USER" -v gen_pass="$GENERATOR_DB_PASSWORD" \
     -v exp_user="$EXPORTER_DB_USER" -v exp_pass="$EXPORTER_DB_PASSWORD" \
     -v db="$POSTGRES_DB" <<'SQL'
-- Application role: order-api and worker
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_pass' CONNECTION LIMIT 40;
GRANT CONNECT ON DATABASE :"db" TO :"app_user";
GRANT USAGE ON SCHEMA public TO :"app_user";
GRANT SELECT ON customers TO :"app_user";
GRANT SELECT, UPDATE ON products TO :"app_user";
GRANT SELECT, INSERT, UPDATE ON orders, order_items, payments TO :"app_user";

-- Load generator role: catalog maintenance, archiving, chaos scenarios
CREATE ROLE :"gen_user" LOGIN PASSWORD :'gen_pass' CONNECTION LIMIT 40;
GRANT CONNECT ON DATABASE :"db" TO :"gen_user";
GRANT USAGE ON SCHEMA public TO :"gen_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"gen_user";

GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO :"app_user", :"gen_user";

-- Monitoring role for postgres-exporter
CREATE ROLE :"exp_user" LOGIN PASSWORD :'exp_pass' CONNECTION LIMIT 5;
GRANT pg_monitor TO :"exp_user";
GRANT CONNECT ON DATABASE :"db" TO :"exp_user";
SQL
