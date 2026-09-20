#!/usr/bin/env bash
# =============================================================================
# UMP Mediation System — Deployment Script
# =============================================================================
# Usage: sudo bash deploy.sh
# Run from the project root on the production server.
# Assumes PostgreSQL databases already exist — runs migrations and seeds only.
# =============================================================================
set -euo pipefail

APP_DIR="/opt/ump/mediation"
VENV_DIR="/opt/ump/venv"
LOG_DIR="/opt/ump/logs"
ENV_FILE="/opt/ump/.env"
APP_USER="ump"
APP_GROUP="ump"

echo "============================================="
echo " UMP Mediation System — Deployment"
echo "============================================="

# ---- 1. Pre-flight checks ----
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Run this script as root (sudo bash deploy.sh)"
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found."
    echo "  Copy deploy/.env.example to $ENV_FILE and fill in production values."
    exit 1
fi

source "$ENV_FILE"

if [ -z "${DJANGO_SECRET_KEY:-}" ]; then
    echo "ERROR: DJANGO_SECRET_KEY is not set in $ENV_FILE"
    exit 1
fi

if [ -z "${DB_PASSWORD:-}" ]; then
    echo "ERROR: DB_PASSWORD is not set in $ENV_FILE"
    exit 1
fi

echo "[1/9] Pre-flight checks passed"

# ---- 2. System user ----
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home /opt/ump "$APP_USER"
    echo "  Created system user: $APP_USER"
else
    echo "  System user $APP_USER already exists"
fi

# ---- 2a. Sudoers rule (allows UI service control via systemctl) ----
SUDOERS_FILE="/etc/sudoers.d/ump-mediation"
cat > "$SUDOERS_FILE" <<'EOF'
# UMP Mediation — allow the ump user to manage mediation services from the web UI
ump ALL=(ALL) NOPASSWD: \
    /usr/bin/systemctl start mediation-*.service, \
    /usr/bin/systemctl stop mediation-*.service, \
    /usr/bin/systemctl restart mediation-*.service, \
    /usr/bin/systemctl is-active mediation-*.service, \
    /usr/bin/systemctl is-enabled mediation-*.service
EOF
chmod 440 "$SUDOERS_FILE"
echo "  Sudoers rule installed: $SUDOERS_FILE"

# ---- 3. Directory structure ----
mkdir -p "$APP_DIR" "$LOG_DIR"
UMP_DATA="${UMP_STORAGE_ROOT:-/opt/ump/data}"
mkdir -p "$UMP_DATA"/{landing/input,landing/output,processing,archive/input,archive/output,error,quarantine}
chown -R "$APP_USER:$APP_GROUP" /opt/ump
echo "[2/9] Directories verified"

# ---- 4. Copy application code (production-essential files only) ----
# Exclude list lives in deploy/.rsync-exclude — edit there, not here.
EXCLUDE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.rsync-exclude"
if [ ! -f "$EXCLUDE_FILE" ]; then
    EXCLUDE_FILE="deploy/.rsync-exclude"
fi
rsync -a --delete --exclude-from="$EXCLUDE_FILE" ./ "$APP_DIR/"

chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
echo "[3/9] Application code deployed"

# ---- 5. Python virtual environment ----
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created virtual environment"
fi

"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet
chown -R "$APP_USER:$APP_GROUP" "$VENV_DIR"
echo "[4/9] Python dependencies installed"

# ---- 6. Database migrations (existing databases) ----
echo "[5/9] Running database migrations on existing databases..."

# Main database (default)
sudo -u "$APP_USER" bash -c "
    source $ENV_FILE
    cd $APP_DIR
    $VENV_DIR/bin/python manage.py migrate --noinput
"
echo "  Migrated default database"

# Per-operator databases
sudo -u "$APP_USER" bash -c "
    source $ENV_FILE
    cd $APP_DIR
    $VENV_DIR/bin/python manage.py provision_operator --all
" || echo "  WARNING: provision_operator had issues — check output above"
echo "  Migrated operator databases"

# ---- 7. Seed reference data ----
echo "[6/9] Seeding reference data..."
sudo -u "$APP_USER" bash -c "
    source $ENV_FILE
    cd $APP_DIR
    $VENV_DIR/bin/python manage.py seed_operators
"
echo "  Operators and source patterns seeded"

# ---- 8. Collect static files ----
sudo -u "$APP_USER" bash -c "
    source $ENV_FILE
    cd $APP_DIR
    $VENV_DIR/bin/python manage.py collectstatic --noinput --clear
"
echo "[7/9] Static files collected"

# ---- 9. Django deployment checks ----
echo "[8/9] Running Django deployment checks..."
sudo -u "$APP_USER" bash -c "
    source $ENV_FILE
    cd $APP_DIR
    $VENV_DIR/bin/python manage.py check --deploy
" || echo "  WARNING: Some deployment checks failed. Review output above."

# ---- 10. Install and restart systemd services ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for unit in mediation-api.service mediation-collector.service mediation-decoder.service mediation-distributor.service mediation.target; do
    if [ -f "$SCRIPT_DIR/$unit" ]; then
        cp "$SCRIPT_DIR/$unit" /etc/systemd/system/
    elif [ -f "deploy/$unit" ]; then
        cp "deploy/$unit" /etc/systemd/system/
    fi
done

systemctl daemon-reload
systemctl enable mediation.target

# Stop services first for clean restart
systemctl stop mediation-collector mediation-decoder mediation-distributor mediation-api 2>/dev/null || true

systemctl start mediation-api
systemctl start mediation-collector
systemctl start mediation-decoder
systemctl start mediation-distributor

echo "[9/9] Services restarted"

# ---- Verify ----
echo ""
echo "Verifying services..."
sleep 3
ALL_OK=true
for svc in mediation-api mediation-collector mediation-decoder mediation-distributor; do
    status=$(systemctl is-active "$svc" 2>/dev/null || true)
    if [ "$status" = "active" ]; then
        echo "  OK  $svc"
    else
        echo "  FAIL  $svc ($status) — check: journalctl -u $svc -n 30"
        ALL_OK=false
    fi
done

echo ""
echo "============================================="
if $ALL_OK; then
    echo " Deployment successful!"
else
    echo " Deployment completed with warnings — check failed services above"
fi
echo "============================================="
echo ""
echo "Post-deployment:"
echo "  Nginx setup:    cp deploy/nginx.conf /etc/nginx/sites-available/ump-mediation"
echo "                  ln -s /etc/nginx/sites-available/ump-mediation /etc/nginx/sites-enabled/"
echo "                  nginx -t && systemctl reload nginx"
echo "  Create admin:   sudo -u ump $VENV_DIR/bin/python $APP_DIR/manage.py createsuperuser"
echo "  Monitor logs:   journalctl -u mediation-api -f"
echo "  All services:   systemctl status mediation.target"
echo ""
