#!/usr/bin/env bash
# =============================================================================
# UMP Mediation System — Update Deployment
# =============================================================================
# Deploys latest code changes to an already-running production server.
# Usage: sudo bash deploy/update.sh
# =============================================================================
set -euo pipefail

APP_DIR="/opt/ump/mediation"
VENV_DIR="/opt/ump/venv"
ENV_FILE="/opt/ump/.env"
APP_USER="ump"

echo "=== UMP Mediation — Updating ==="

# ---- 1. Sync code (production-essential files only) ----
# Exclude list lives in deploy/.rsync-exclude — edit there, not here.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXCLUDE_FILE="$SCRIPT_DIR/.rsync-exclude"
if [ ! -f "$EXCLUDE_FILE" ]; then
    EXCLUDE_FILE="deploy/.rsync-exclude"
fi
rsync -a --delete --exclude-from="$EXCLUDE_FILE" ./ "$APP_DIR/"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
echo "[1/5] Code synced"

# ---- 2. Install new dependencies (if any) ----
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet
echo "[2/5] Dependencies updated"

# ---- 3. Migrate (applies only new migrations) ----
sudo -u "$APP_USER" bash -c "
    source $ENV_FILE
    cd $APP_DIR
    $VENV_DIR/bin/python manage.py migrate --noinput
"
echo "[3/5] Migrations applied"

# ---- 4. Collect static files ----
sudo -u "$APP_USER" bash -c "
    source $ENV_FILE
    cd $APP_DIR
    $VENV_DIR/bin/python manage.py collectstatic --noinput
"
echo "[4/5] Static files collected"

# ---- 5. Restart services ----
systemctl restart mediation-api
systemctl restart mediation-collector
systemctl restart mediation-decoder
systemctl restart mediation-distributor
echo "[5/5] Services restarted"

# ---- Verify ----
sleep 3
for svc in mediation-api mediation-collector mediation-decoder mediation-distributor; do
    status=$(systemctl is-active "$svc" 2>/dev/null || true)
    if [ "$status" = "active" ]; then
        echo "  OK  $svc"
    else
        echo "  FAIL  $svc — journalctl -u $svc -n 30"
    fi
done

echo ""
echo "=== Update complete ==="
