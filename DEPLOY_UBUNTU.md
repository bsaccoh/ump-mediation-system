# UMP Mediation — Ubuntu 26.04 Server Deployment Guide

Server: 8-core 3.4GHz, 8GB RAM, 500GB SSD
Storage: 3TB Synology NAS (backup/archive)

---

## STEP 1: System Packages

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
  python3 python3-pip python3-venv python3-dev \
  postgresql postgresql-contrib libpq-dev \
  nginx \
  build-essential \
  git curl wget unzip \
  nfs-common \
  supervisor
```

Check versions:

```bash
python3 --version    # expect 3.13+
psql --version       # expect 17+
nginx -v
```

---

## STEP 2: Create System User

```bash
sudo useradd --system --shell /bin/bash --home /opt/ump --create-home ump
sudo chmod 750 /opt/ump
```

---

## STEP 3: PostgreSQL Setup

```bash
sudo -u postgres psql
```

Inside the psql shell, run:

```sql
-- Create the database user
CREATE USER ump_user WITH PASSWORD 'your-strong-password-here';

-- Control plane database
CREATE DATABASE ump_mediation OWNER ump_user;

-- Per-operator CDR stream databases
CREATE DATABASE ump_mediation_orange OWNER ump_user;
CREATE DATABASE ump_mediation_africell OWNER ump_user;
CREATE DATABASE ump_mediation_qcell OWNER ump_user;

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE ump_mediation TO ump_user;
GRANT ALL PRIVILEGES ON DATABASE ump_mediation_orange TO ump_user;
GRANT ALL PRIVILEGES ON DATABASE ump_mediation_africell TO ump_user;
GRANT ALL PRIVILEGES ON DATABASE ump_mediation_qcell TO ump_user;

\q
```

Tune PostgreSQL for 8GB RAM — edit `/etc/postgresql/17/main/postgresql.conf`:

```bash
sudo nano /etc/postgresql/17/main/postgresql.conf
```

Set these values:

```
shared_buffers = 2GB
effective_cache_size = 4GB
work_mem = 64MB
maintenance_work_mem = 256MB
max_connections = 100
```

Restart PostgreSQL:

```bash
sudo systemctl restart postgresql
sudo systemctl enable postgresql
```

---

## STEP 4: Transfer Project to Server

**Option A — From your Windows PC via SCP:**

```bash
# Run this on your Windows machine (PowerShell):
scp -r "C:\Users\Saccoh1629182\Documents\Babah\BS\OCS\project\babah\ump-mediation-system" your-user@server-ip:/tmp/ump-src
```

**Option B — If using Git:**

```bash
# On the server:
sudo -u ump git clone <your-repo-url> /opt/ump/mediation
```

**After transfer — move into place:**

```bash
sudo mv /tmp/ump-src /opt/ump/mediation
sudo chown -R ump:ump /opt/ump/mediation
```

---

## STEP 5: Create Directory Structure

```bash
sudo -u ump bash << 'EOF'
# Data directories (on SSD)
mkdir -p /opt/ump/data/{incoming,decoded,processed,failed,archive}/{orange,africell,qcell}

# Per-operator sub-dirs for each stream type
for op in orange africell qcell; do
  for stream in msc ims pgw sgsn sgw cbs; do
    mkdir -p /opt/ump/data/incoming/$op/$stream
    mkdir -p /opt/ump/data/decoded/$op/$stream
    mkdir -p /opt/ump/data/processed/$op/$stream
    mkdir -p /opt/ump/data/failed/$op/$stream
  done
done

# Logs, static, media
mkdir -p /opt/ump/logs
mkdir -p /opt/ump/static_collected
mkdir -p /opt/ump/media
EOF
```

---

## STEP 6: Python Virtual Environment & Dependencies

```bash
sudo -u ump bash << 'EOF'
cd /opt/ump
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r mediation/requirements.txt
pip install gunicorn psycopg2-binary
EOF
```

---

## STEP 7: Environment File

```bash
sudo -u ump nano /opt/ump/.env
```

Paste this content (edit the values):

```
# Django
DJANGO_SECRET_KEY=change-this-to-a-50-char-random-string
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=your-server-ip,localhost,127.0.0.1

# PostgreSQL
DB_ENGINE=django.db.backends.postgresql
DB_USER=ump_user
DB_PASSWORD=your-strong-password-here
DB_HOST=localhost
DB_PORT=5432
DB_NAME=ump_mediation

# Operators
OPERATORS=orange,africell,qcell
DEFAULT_OPERATOR=orange

# Mediation mode — decode only, no CDR record DB inserts
CDR_PERSIST_RECORDS=False

# Service mode — independent systemd services (collector/decoder/distributor/api)
SERVICE_MODE=True
SERVICE_POLL_INTERVAL=10
LOG_DIR=/opt/ump/logs

# Celery — disable for now (no Redis needed)
USE_CELERY=False
```

Generate a random secret key:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Copy the output into `DJANGO_SECRET_KEY`.

---

## STEP 8: Django Setup (Migrations + Superuser + Static)

```bash
sudo -u ump bash << 'EOF'
cd /opt/ump/mediation
source /opt/ump/venv/bin/activate
set -a; source /opt/ump/.env; set +a

# Run migrations on all databases
python manage.py migrate                              # default (control plane)
python manage.py migrate --database=mediation_orange   # orange CDR streams
python manage.py migrate --database=mediation_africell # africell CDR streams
python manage.py migrate --database=mediation_qcell    # qcell CDR streams

# Create admin superuser
python manage.py createsuperuser
# Enter: username, email, password when prompted

# Collect static files for Nginx
python manage.py collectstatic --noinput -c

# Seed reference data (if seed scripts exist)
python manage.py shell -c "
from reference.models import Operator
if not Operator.objects.exists():
    Operator.objects.create(code='orange', name='Orange SL', country='Sierra Leone', mcc='619', mnc='01', is_active=True)
    Operator.objects.create(code='africell', name='Africell SL', country='Sierra Leone', mcc='619', mnc='03', is_active=True)
    Operator.objects.create(code='qcell', name='QCell SL', country='Sierra Leone', mcc='619', mnc='05', is_active=True)
    print('Operators seeded')
else:
    print('Operators already exist')
"
EOF
```

**Quick test — verify Django starts:**

```bash
sudo -u ump bash -c '
  cd /opt/ump/mediation
  source /opt/ump/venv/bin/activate
  set -a; source /opt/ump/.env; set +a
  python manage.py check --deploy
'
```

Fix any warnings it reports before continuing.

---

## STEP 9: Mediation Services (Independent Processes)

The platform runs as four independent systemd services + a target to manage them together:

| Service | What it does | Log file |
|---------|-------------|----------|
| `mediation-collector` | Scans input dirs, registers CDRFile(COLLECTED) | `collection.log` |
| `mediation-decoder` | Decodes/validates/enriches → CDRFile(DECODED) | `decoder.log` |
| `mediation-distributor` | Dispatches via output portals → CDRFile(COMPLETED) | `distributor.log` |
| `mediation-api` | Gunicorn web dashboard + REST API | `api.log` |

**Install all service files:**

```bash
sudo cp /opt/ump/mediation/deploy/mediation-collector.service /etc/systemd/system/
sudo cp /opt/ump/mediation/deploy/mediation-decoder.service /etc/systemd/system/
sudo cp /opt/ump/mediation/deploy/mediation-distributor.service /etc/systemd/system/
sudo cp /opt/ump/mediation/deploy/mediation-api.service /etc/systemd/system/
sudo cp /opt/ump/mediation/deploy/mediation.target /etc/systemd/system/
```

**Enable and start everything:**

```bash
sudo systemctl daemon-reload

# Enable all services to start on boot
sudo systemctl enable mediation.target
sudo systemctl enable mediation-collector mediation-decoder mediation-distributor mediation-api

# Start all services at once
sudo systemctl start mediation.target

# Check status of all services
sudo systemctl status mediation-collector mediation-decoder mediation-distributor mediation-api
```

**Individual service control:**

```bash
# Stop/start/restart a single service without affecting others
sudo systemctl stop mediation-decoder
sudo systemctl start mediation-decoder
sudo systemctl restart mediation-distributor

# Stop ALL mediation services
sudo systemctl stop mediation.target

# Start ALL mediation services
sudo systemctl start mediation.target
```

**View logs:**

```bash
# Per-service log files (rotating, 50MB each, 5 backups)
tail -f /opt/ump/logs/collection.log
tail -f /opt/ump/logs/decoder.log
tail -f /opt/ump/logs/distributor.log
tail -f /opt/ump/logs/api.log

# Via journalctl (systemd journal)
journalctl -u mediation-collector -f
journalctl -u mediation-decoder -f --since "10 min ago"
journalctl -u mediation-distributor --no-pager -n 50
journalctl -u mediation-api -f
```

---

## STEP 10: Nginx Reverse Proxy

```bash
sudo nano /etc/nginx/sites-available/ump
```

Paste:

```nginx
server {
    listen 80;
    server_name your-server-ip your-domain.com;

    client_max_body_size 100M;

    # Static files — served directly by Nginx
    location /static/ {
        alias /opt/ump/static_collected/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Media files
    location /media/ {
        alias /opt/ump/media/;
        expires 7d;
    }

    # Everything else → Gunicorn
    location / {
        proxy_pass http://unix:/opt/ump/gunicorn.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

Enable and start:

```bash
sudo ln -s /etc/nginx/sites-available/ump /etc/nginx/sites-enabled/ump
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl enable nginx
```

---

## STEP 11: Firewall (UFW)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

---

## STEP 12: Synology NFS Mount

**On the Synology:**
1. Control Panel → Shared Folder → Create `ump-archive`
2. Control Panel → File Services → Enable NFS
3. Shared Folder → `ump-archive` → NFS Permissions → Add:
   - Server IP: your Ubuntu server IP
   - Privilege: Read/Write
   - Squash: Map all users to admin

**On the Ubuntu server:**

```bash
# Create mount point
sudo mkdir -p /mnt/synology

# Test mount
sudo mount -t nfs synology-ip:/volume1/ump-archive /mnt/synology

# Verify
df -h /mnt/synology

# Make permanent — add to fstab
echo "synology-ip:/volume1/ump-archive /mnt/synology nfs defaults,_netdev,nofail 0 0" | sudo tee -a /etc/fstab
```

---

## STEP 13: Nightly Archive Sync (Cron)

```bash
sudo -u ump crontab -e
```

Add this line:

```
# Sync processed CDR archives to Synology every night at 2 AM
0 2 * * * rsync -av --remove-source-files /opt/ump/data/archive/ /mnt/synology/$(date +\%Y)/$(date +\%m)/ >> /opt/ump/logs/archive-sync.log 2>&1
```

---

## STEP 14: First Test

**1. Open in browser:**

```
http://your-server-ip/
```

Login with the superuser you created.

**2. Upload a test CDR file:**

- Go to Operations → File Upload
- Select the operator (Orange)
- Select the stream type (MSC, PGW, etc.)
- Upload a CDR file

**3. Check processing:**

- Go to Operations → File Manager — file should show COMPLETED
- Check decoded output on disk:

```bash
ls -la /opt/ump/data/decoded/orange/msc/
```

**4. Check logs if something fails:**

```bash
# Application logs
tail -f /opt/ump/logs/gunicorn-error.log

# Django debug (run manually)
sudo -u ump bash -c '
  cd /opt/ump/mediation
  source /opt/ump/venv/bin/activate
  set -a; source /opt/ump/.env; set +a
  python manage.py runserver 0.0.0.0:8000
'
```

---

## USEFUL COMMANDS

```bash
# Restart all services after code changes
sudo systemctl restart mediation.target

# Restart just the API after code changes
sudo systemctl restart mediation-api

# View live logs per service
tail -f /opt/ump/logs/collection.log
tail -f /opt/ump/logs/decoder.log
tail -f /opt/ump/logs/distributor.log
tail -f /opt/ump/logs/api.log

# Django shell
sudo -u ump bash -c 'cd /opt/ump/mediation && source /opt/ump/venv/bin/activate && set -a && source /opt/ump/.env && set +a && python manage.py shell'

# Check disk usage
du -sh /opt/ump/data/*

# Check PostgreSQL databases
sudo -u postgres psql -c "\l" | grep ump

# Process a file manually via CLI
sudo -u ump bash -c '
  cd /opt/ump/mediation
  source /opt/ump/venv/bin/activate
  set -a; source /opt/ump/.env; set +a
  python manage.py shell -c "
from collection.models import CDRFile
files = CDRFile.objects.filter(status=\"PENDING\")
print(f\"{files.count()} files pending\")
"
'
```

---

## LATER: Enable Celery (Optional)

When you want automated SFTP polling and scheduled collection:

```bash
# Install Redis
sudo apt install -y redis-server
sudo systemctl enable redis-server

# Update .env
# USE_CELERY=True
# CELERY_BROKER_URL=redis://localhost:6379/0

# Create Celery worker service
sudo nano /etc/systemd/system/ump-celery.service
```

```ini
[Unit]
Description=UMP Celery Worker
After=network.target redis-server.service postgresql.service

[Service]
User=ump
Group=ump
WorkingDirectory=/opt/ump/mediation
EnvironmentFile=/opt/ump/.env
ExecStart=/opt/ump/venv/bin/celery -A config worker \
    --loglevel=info \
    --concurrency=4 \
    --logfile=/opt/ump/logs/celery.log
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ump-celery
sudo systemctl start ump-celery
```
