#!/opt/ump/venv/bin/python
"""
UMP Mediation — Operations Dashboard
Usage: ump_status [all|services|health|cdr|restart|stop|start]
"""
import os
import sys
import subprocess
import datetime

# ── Load .env before Django setup ────────────────────────────────────────────
_ENV = '/opt/ump/.env'
if os.path.exists(_ENV):
    with open(_ENV) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _, _v = _line.partition('=')
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── Django setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, '/opt/ump/mediation')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

_DB_OK = False
try:
    import django
    django.setup()
    _DB_OK = True
except Exception as _e:
    _DB_SETUP_ERR = str(_e)
# ─────────────────────────────────────────────────────────────────────────────

# ANSI colour helpers
R  = '\033[0;31m'
G  = '\033[0;32m'
Y  = '\033[1;33m'
C  = '\033[0;36m'
W  = '\033[1m'
DIM = '\033[2m'
N  = '\033[0m'

SERVICES = [
    'mediation-api',
    'mediation-collector',
    'mediation-decoder',
    'mediation-distributor',
    'postgresql',
    'nginx',
]

MEDIATION_SERVICES = [
    'mediation-api',
    'mediation-collector',
    'mediation-decoder',
    'mediation-distributor',
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def bar(pct: float, width: int = 22) -> str:
    pct = max(0, min(100, pct))
    filled = int(pct * width / 100)
    colour = G if pct < 70 else Y if pct < 90 else R
    return f'{colour}[{"█" * filled}{"░" * (width - filled)}]{N}'


def col(text: str, width: int, align: str = '<') -> str:
    """Pad text ignoring embedded ANSI codes."""
    import re
    visible = re.sub(r'\033\[[0-9;]*m', '', text)
    pad = width - len(visible)
    if align == '>':
        return ' ' * max(pad, 0) + text
    return text + ' ' * max(pad, 0)


def rule(widths: list[int], char: str = '─') -> str:
    return '  ' + '  '.join(char * w for w in widths)


def header() -> None:
    now = datetime.datetime.now().strftime('%Y-%m-%d  %H:%M:%S')
    print(f'\n{C}{W}╔══════════════════════════════════════════════════════╗{N}')
    print(f'{C}{W}║{N}  {W}{"UMP MEDIATION  —  SYSTEM STATUS":<52}{C}{W}║{N}')
    print(f'{C}{W}║{N}  {DIM}{now:<52}{N}{C}{W}║{N}')
    print(f'{C}{W}╚══════════════════════════════════════════════════════╝{N}\n')


# ── 1. Services ───────────────────────────────────────────────────────────────

def show_services() -> None:
    W_NAME, W_STATUS, W_SINCE = 28, 10, 32
    print(f'{W}SERVICES{N}')
    print(rule([W_NAME, W_STATUS, W_SINCE]))
    print(f'  {col("Service", W_NAME)}  {col("Status", W_STATUS)}  {col("Active Since", W_SINCE)}')
    print(rule([W_NAME, W_STATUS, W_SINCE]))

    for svc in SERVICES:
        try:
            active = subprocess.check_output(
                ['systemctl', 'is-active', svc],
                stderr=subprocess.DEVNULL, text=True
            ).strip()
        except subprocess.CalledProcessError as e:
            active = (e.output or b'').decode().strip() or 'inactive'
        except FileNotFoundError:
            active = 'unknown'

        try:
            since = subprocess.check_output(
                ['systemctl', 'show', svc,
                 '--property=ActiveEnterTimestamp', '--value'],
                stderr=subprocess.DEVNULL, text=True
            ).strip().replace(' UTC', '')
            since = since or '—'
        except Exception:
            since = '—'

        if active == 'active':
            status_col = f'{G}● ACTIVE{N}'
        elif active == 'inactive':
            status_col = f'{Y}○ INACTIVE{N}'
        else:
            status_col = f'{R}✗ {active.upper()}{N}'

        print(f'  {col(svc, W_NAME)}  {col(status_col, W_STATUS)}  {col(since, W_SINCE)}')

    print(rule([W_NAME, W_STATUS, W_SINCE]))
    print()


# ── 2. System Health ──────────────────────────────────────────────────────────

def show_health() -> None:
    print(f'{W}SYSTEM HEALTH{N}')
    W_LABEL, W_VAL = 20, 55
    print(rule([W_LABEL, W_VAL]))

    rows: list[tuple[str, str]] = []

    # CPU
    try:
        out = subprocess.check_output(
            ['top', '-bn1'], stderr=subprocess.DEVNULL, text=True
        )
        import re
        for line in out.splitlines():
            m = re.search(r'(\d+\.?\d*)\s*(?:%?\s*)?(?:id|idle)', line, re.I)
            if m:
                idle = float(m.group(1))
                used = 100.0 - idle
                rows.append(('CPU', f'{bar(used)}  {used:.1f}%'))
                break
        else:
            rows.append(('CPU', '—'))
    except Exception:
        rows.append(('CPU', '—'))

    # Memory
    try:
        minfo: dict[str, int] = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':')
                minfo[k.strip()] = int(v.strip().split()[0])
        total_mb = minfo['MemTotal'] // 1024
        avail_mb = minfo.get('MemAvailable', minfo.get('MemFree', 0)) // 1024
        used_mb  = total_mb - avail_mb
        pct = used_mb * 100 // total_mb if total_mb else 0
        rows.append((
            'Memory',
            f'{bar(pct)}  {used_mb/1024:.1f} GB / {total_mb/1024:.1f} GB  ({pct}%)',
        ))
    except Exception:
        rows.append(('Memory', '—'))

    # Disks
    for mount in ['/', '/opt/ump/data', '/mnt/synology']:
        if not os.path.exists(mount):
            continue
        try:
            parts = subprocess.check_output(
                ['df', '-h', mount], stderr=subprocess.DEVNULL, text=True
            ).splitlines()[1].split()
            used_h, total_h, pct_s = parts[2], parts[1], parts[4]
            pct = int(pct_s.replace('%', ''))
            rows.append((f'Disk {mount}', f'{bar(pct)}  {used_h} / {total_h}  ({pct_s})'))
        except Exception:
            pass

    # Load average
    try:
        with open('/proc/loadavg') as f:
            loads = f.read().split()[:3]
        rows.append(('Load Avg (1/5/15m)', '  '.join(loads)))
    except Exception:
        pass

    # Uptime
    try:
        with open('/proc/uptime') as f:
            secs = int(float(f.read().split()[0]))
        d, r = divmod(secs, 86400)
        h, r = divmod(r, 3600)
        m = r // 60
        rows.append(('Uptime', f'{d}d  {h}h  {m}m'))
    except Exception:
        pass

    for label, val in rows:
        print(f'  {col(label, W_LABEL)}  {val}')

    print(rule([W_LABEL, W_VAL]))
    print()


# ── 3. CDR Status ─────────────────────────────────────────────────────────────

def show_cdr() -> None:
    print(f'{W}CDR STATUS  —  Last 24 hours{N}')

    if not _DB_OK:
        print(f'  {R}Cannot connect to database.{N}  Check mediation-api service.\n')
        return

    from django.utils import timezone
    from django.db.models import Count, Q
    from collection.models import CDRFile, DistributionLog

    since = timezone.now() - datetime.timedelta(hours=24)
    qs = CDRFile.objects.filter(created_at__gte=since)

    # ── Overall totals ────────────────────────────────────────────────────────
    totals = qs.aggregate(
        total     = Count('id'),
        collected = Count('id', filter=Q(status='COLLECTED')),
        pending   = Count('id', filter=Q(status__in=['PROCESSING', 'DECODED', 'DISPATCHING'])),
        completed = Count('id', filter=Q(status='COMPLETED')),
        failed    = Count('id', filter=Q(status='FAILED')),
        duplicate = Count('id', filter=Q(status='DUPLICATE')),
        empty     = Count('id', filter=Q(status='EMPTY')),
    )

    print(f'\n  {W}Overall (all operators){N}')
    cols = [10, 12, 12, 12, 10, 12, 8]
    print(rule(cols))
    print(f'  {"Total":<10}  {"Collected":<12}  {"Pending":<12}  {"Completed":<12}  {"Failed":<10}  {"Duplicate":<12}  {"Empty":<8}')
    print(rule(cols))

    def _c(val, warn_colour=None):
        if warn_colour and val:
            return f'{warn_colour}{val}{N}'
        return str(val)

    print(
        f'  {col(str(totals["total"]), 10)}'
        f'  {col(str(totals["collected"]), 12)}'
        f'  {col(_c(totals["pending"], Y), 12)}'
        f'  {col(_c(totals["completed"], G), 12)}'
        f'  {col(_c(totals["failed"], R), 10)}'
        f'  {col(_c(totals["duplicate"], Y), 12)}'
        f'  {col(str(totals["empty"]), 8)}'
    )
    print(rule(cols))

    # ── By operator ───────────────────────────────────────────────────────────
    print(f'\n  {W}By Operator{N}')
    by_op = (
        qs.values('operator_code')
          .annotate(
              total     = Count('id'),
              completed = Count('id', filter=Q(status='COMPLETED')),
              failed    = Count('id', filter=Q(status='FAILED')),
              pending   = Count('id', filter=Q(status__in=['PROCESSING', 'DECODED', 'DISPATCHING'])),
          )
          .order_by('operator_code')
    )
    cols2 = [14, 8, 12, 10, 10]
    print(rule(cols2))
    print(f'  {"Operator":<14}  {"Total":<8}  {"Completed":<12}  {"Pending":<10}  {"Failed":<10}')
    print(rule(cols2))
    if by_op:
        for row in by_op:
            op = (row['operator_code'] or '—').upper()
            print(
                f'  {col(op, 14)}'
                f'  {col(str(row["total"]), 8)}'
                f'  {col(_c(row["completed"], G), 12)}'
                f'  {col(_c(row["pending"], Y), 10)}'
                f'  {col(_c(row["failed"], R), 10)}'
            )
    else:
        print(f'  {DIM}No files in the last 24h{N}')
    print(rule(cols2))

    # ── Distribution by portal ────────────────────────────────────────────────
    print(f'\n  {W}Distribution by Portal{N}')
    portal_stats = (
        DistributionLog.objects
        .filter(delivered_at__gte=since)
        .values('output_portal__name')
        .annotate(
            total   = Count('id'),
            success = Count('id', filter=Q(status='SUCCESS')),
            failed  = Count('id', filter=Q(status='FAILED')),
            skipped = Count('id', filter=Q(status='SKIPPED')),
            records = Count('record_count'),
        )
        .order_by('output_portal__name')
    )

    cols3 = [26, 8, 10, 10, 10]
    print(rule(cols3))
    print(f'  {"Portal":<26}  {"Total":<8}  {"Success":<10}  {"Failed":<10}  {"Skipped":<10}')
    print(rule(cols3))
    if portal_stats:
        for row in portal_stats:
            name = (row['output_portal__name'] or 'Unknown')[:26]
            print(
                f'  {col(name, 26)}'
                f'  {col(str(row["total"]), 8)}'
                f'  {col(_c(row["success"], G), 10)}'
                f'  {col(_c(row["failed"], R), 10)}'
                f'  {col(str(row["skipped"]), 10)}'
            )
    else:
        print(f'  {DIM}No distribution activity in the last 24h{N}')
    print(rule(cols3))

    # ── Errors (most recent 5) ────────────────────────────────────────────────
    errors = (
        CDRFile.objects
        .filter(status='FAILED', created_at__gte=since)
        .order_by('-created_at')[:5]
    )
    if errors:
        print(f'\n  {W}Recent Errors{N}')
        print(rule([18, 12, 40]))
        print(f'  {"Time":<18}  {"Operator":<12}  {"File":<40}')
        print(rule([18, 12, 40]))
        for f in errors:
            ts  = f.created_at.strftime('%Y-%m-%d %H:%M') if f.created_at else '—'
            op  = (f.operator_code or '—').upper()
            fn  = (f.filename or '—')[-40:]
            print(f'  {col(ts, 18)}  {col(op, 12)}  {R}{fn}{N}')
        print(rule([18, 12, 40]))

    print()


# ── 4. Service control ────────────────────────────────────────────────────────

def _systemctl(action: str, services: list[str]) -> None:
    for svc in services:
        try:
            subprocess.run(['sudo', 'systemctl', action, svc], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            icon = G + '●' + N if action == 'start' else Y + '○' + N if action == 'stop' else C + '↻' + N
            print(f'  {icon}  {svc}  {action}ed')
        except subprocess.CalledProcessError:
            print(f'  {R}✗{N}  {svc}  ({action} failed)')
        except FileNotFoundError:
            print(f'  {R}✗{N}  sudo/systemctl not found')
            break


def restart_services(target: list[str] | None = None) -> None:
    svcs = target or MEDIATION_SERVICES
    print(f'\n{W}Restarting services...{N}')
    _systemctl('restart', svcs)
    print()


def stop_services() -> None:
    print(f'\n{W}Stopping services...{N}')
    _systemctl('stop', list(reversed(MEDIATION_SERVICES)))
    print()


def start_services() -> None:
    print(f'\n{W}Starting services...{N}')
    _systemctl('start', MEDIATION_SERVICES)
    print()


# ── Usage ─────────────────────────────────────────────────────────────────────

def usage() -> None:
    print(f"""
  {W}Usage:{N}  ump_status [command]

  {W}Commands:{N}
    {G}all{N}        Show services + health + CDR status  (default)
    {G}services{N}   Show service status table only
    {G}health{N}     Show system health (CPU / memory / disk) only
    {G}cdr{N}        Show CDR processing status only

    {Y}restart{N}    Restart all four mediation services
    {Y}stop{N}       Stop all four mediation services
    {Y}start{N}      Start all four mediation services

  {DIM}Examples:{N}
    ump_status                   # full dashboard
    ump_status services          # just services
    ump_status cdr               # just CDR stats
    ump_status restart           # restart mediation services
""")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else 'all'

    if cmd in ('all', 'services', 'health', 'cdr'):
        header()

    if cmd == 'all':
        show_services()
        show_health()
        show_cdr()
    elif cmd == 'services':
        show_services()
    elif cmd == 'health':
        show_health()
    elif cmd == 'cdr':
        show_cdr()
    elif cmd == 'restart':
        restart_services()
    elif cmd == 'stop':
        stop_services()
    elif cmd == 'start':
        start_services()
    else:
        usage()


if __name__ == '__main__':
    main()
