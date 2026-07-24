#!/usr/bin/env bash
# Per-service database bootstrap.
#
# Creates the three service databases (interconnect, regulatory, roaming),
# runs each app's migrations to its assigned database, and re-seeds the
# demo data.  ump_mediation must already exist (the historical main DB).
#
# Requires PG superuser credentials (PGUSER / PGPASSWORD env vars) for the
# CREATE DATABASE step; everything else runs as the app user.
#
# Usage::
#   PG_SUPER=postgres PG_SUPER_PWD=root123 ./scripts/split_databases.sh
set -euo pipefail

PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_SUPER="${PG_SUPER:-postgres}"
PG_SUPER_PWD="${PG_SUPER_PWD:-root123}"
APP_USER="${DB_USER:-ump_user}"

# 1. Create the three new DBs
for db in ump_interconnect ump_regulatory ump_roaming; do
    PGPASSWORD="$PG_SUPER_PWD" psql -h "$PG_HOST" -p "$PG_PORT" \
        -U "$PG_SUPER" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$db'" \
        | grep -q 1 \
        || PGPASSWORD="$PG_SUPER_PWD" psql -h "$PG_HOST" -p "$PG_PORT" \
            -U "$PG_SUPER" -d postgres \
            -c "CREATE DATABASE $db OWNER $APP_USER;"
done

# 2. Run each app's migrations against its target DB
python manage.py migrate --database=default
python manage.py migrate --database=interconnect
python manage.py migrate --database=regulatory
python manage.py migrate --database=roaming

# 3. Seed demo data
python manage.py seed_interconnect_demo --reset
python manage.py seed_regulatory_demo   --reset
python manage.py seed_roaming_demo      --reset --generate
