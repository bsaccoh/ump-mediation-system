#!/usr/bin/env bash
# =============================================================================
# UMP Mediation — PostgreSQL Setup (only if databases don't exist yet)
# =============================================================================
# Skip this if your production databases already exist.
# The deploy.sh script handles migrations and seeding on existing databases.
#
# Usage (first-time only): sudo -u postgres bash setup_postgres.sh <db_password>
# =============================================================================
set -euo pipefail

DB_USER="ump_user"
DB_PASS="${1:-}"

if [ -z "$DB_PASS" ]; then
    echo "Usage: sudo -u postgres bash setup_postgres.sh <db_password>"
    echo ""
    echo "NOTE: Skip this entirely if your databases already exist."
    echo "      deploy.sh will migrate and seed the existing databases."
    exit 1
fi

echo "Creating PostgreSQL user and databases for UMP Mediation..."

psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || \
    echo "  User $DB_USER already exists"

for DB in ump_mediation ump_mediation_orange ump_mediation_africell ump_mediation_qcell ump_mediation_sierratel; do
    psql -c "CREATE DATABASE $DB OWNER $DB_USER;" 2>/dev/null || \
        echo "  Database $DB already exists"
    psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB TO $DB_USER;"
done

echo ""
echo "Done. Now run: sudo bash deploy.sh"
