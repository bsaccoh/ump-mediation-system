#!/opt/ump/venv/bin/python
# UMP Mediation — Operations CLI
# Usage: ump <command> [options]

import os, sys, re, subprocess, datetime, time, shutil, glob

# ── env + Django setup ────────────────────────────────────────────────────────
_ENV = '/opt/ump/.env'
if os.path.exists(_ENV):
    with open(_ENV) as _f:
        for _l in _f:
            _l = _l.strip()
            if _l and not _l.startswith('#') and '=' in _l:
                k, _, v = _l.partition('=')
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, '/opt/ump/mediation')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

_db_ready = False
try:
    import django; django.setup(); _db_ready = True
except Exception:
    pass

# ── colours ───────────────────────────────────────────────────────────────────
G   = '\033[0;32m'
R   = '\033[0;31m'
Y   = '\033[1;33m'
B   = '\033[1m'
DIM = '\033[2m'
N   = '\033[0m'

# ── services ──────────────────────────────────────────────────────────────────
ALL_SERVICES       = ['postgresql', 'nginx', 'mediation-api', 'mediation-collector',
                      'mediation-decoder', 'mediation-distributor']
MEDIATION_SERVICES = ['mediation-api', 'mediation-collector',
                      'mediation-decoder', 'mediation-distributor']
SERVICE_ALIASES    = {
    'api':           'mediation-api',
    'collector':     'mediation-collector',
    'decoder':       'mediation-decoder',
    'distributor':   'mediation-distributor',
    'mediation-api':          'mediation-api',
    'mediation-collector':    'mediation-collector',
    'mediation-decoder':      'mediation-decoder',
    'mediation-distributor':  'mediation-distributor',
}

# ── layout ────────────────────────────────────────────────────────────────────
STA_W = 72   # inner width for status / health box
CDR_W = 96   # inner width for CDR box (wider to fit 8 columns)

def _vis(s: str) -> str:
    return re.sub(r'\033\[[0-9;]*m', '', s)

def lc(s: str, w: int) -> str:
    return s + ' ' * max(0, w - len(_vis(s)))

def num(n) -> str:
    try:
        return f'{int(n):,}'
    except (TypeError, ValueError):
        return '—'

def box_header(title: str, subtitle: str = '', W: int = STA_W) -> None:
    bar = '═' * W
    print(f'\n╔{bar}╗')
    print(f'║{title.center(W)}║')
    if subtitle:
        print(f'║{subtitle.center(W)}║')
    print(f'╚{bar}╝\n')

def section(name: str, W: int = STA_W) -> None:
    print(f' {B}{name}{N}')
    print(' ' + '─' * W)

# ── subprocess helper ─────────────────────────────────────────────────────────
def _run(cmd, default: str = '') -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return default

# ── service helpers ───────────────────────────────────────────────────────────
def svc_active(name: str) -> str:
    return _run(['systemctl', 'is-active', name])

def svc_pid(name: str) -> str:
    pid = _run(['systemctl', 'show', name, '--property=MainPID', '--value'])
    return pid if pid and pid != '0' else '—'

def svc_uptime(name: str) -> str:
    ts = _run(['systemctl', 'show', name, '--property=ActiveEnterTimestamp', '--value'])
    if not ts:
        return '—'
    clean = re.sub(r'\s+(UTC|BST|WAT|EST|GMT|CAT|EAT|SAST)$', '', ts)
    for fmt in ('%a %Y-%m-%d %H:%M:%S', '%a %Y-%m-%d %H:%M:%S %Z'):
        try:
            dt = datetime.datetime.strptime(clean.strip(), fmt)
            secs = int((datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - dt).total_seconds())
            if secs < 0:
                return '—'
            d, r = divmod(secs, 86400); h, r = divmod(r, 3600); m = r // 60
            parts = ([f'{d}d'] if d else []) + ([f'{h}h'] if h else []) + [f'{m}m']
            return ' '.join(parts)
        except ValueError:
            continue
    return '—'

def status_badge(state: str) -> str:
    if state == 'active':
        return f'{G}● RUNNING{N}'
    elif state in ('inactive', 'dead'):
        return f'{DIM}○ STOPPED{N}'
    elif state == 'failed':
        return f'{R}✗ FAILED {N}'
    return f'{Y}⚠ {state.upper()}{N}'

# ── database check ────────────────────────────────────────────────────────────
def _db_status() -> dict:
    if not _db_ready:
        return {'ok': False, 'error': 'Django not loaded'}
    try:
        from django.db import connection
        t0 = time.time()
        with connection.cursor() as c:
            c.execute(
                "SELECT count(*), current_database() FROM pg_stat_activity WHERE state = 'active'"
            )
            conn_count, dbname = c.fetchone()
        ms = int((time.time() - t0) * 1000)
        return {'ok': True, 'dbname': dbname, 'connections': conn_count, 'ms': ms}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)[:80]}

# ── STATUS command ─────────────────────────────────────────────────────────────
def show_status() -> None:
    LINE = '─' * 40

    # Version
    print(f'\n{B}UMP Mediation Platform v1.10{N}')
    print(LINE)

    # Services — compact table
    DISPLAY_NAMES = {
        'mediation-api': 'API',
        'mediation-collector': 'Collector',
        'mediation-decoder': 'Decoder',
        'mediation-distributor': 'Distributor',
        'postgresql': 'PostgreSQL',
        'nginx': 'Nginx',
    }
    any_down = False
    for svc in ALL_SERVICES:
        name   = DISPLAY_NAMES.get(svc, svc)
        state  = svc_active(svc)
        if state != 'active':
            any_down = True
        if state == 'active':
            badge = f'{G}RUNNING{N}'
            up    = svc_uptime(svc)
            extra = f'   uptime {up}' if svc in MEDIATION_SERVICES else ''
        elif state == 'failed':
            badge = f'{R}FAILED{N}'
            extra = ''
        else:
            badge = f'{DIM}STOPPED{N}'
            extra = ''
        print(f'{lc(name, 16)}{badge}{extra}')

    # Database check
    db = _db_status()
    if not db['ok']:
        print(f'{"PostgreSQL DB":<16}{R}DISCONNECTED{N}')
        any_down = True

    print(LINE)

    # CDR summary for today
    if _db_ready:
        try:
            from django.db.models import Sum, Count
            from django.utils import timezone
            from collection.models import CDRFile
            today = timezone.now().date()
            agg = CDRFile.objects.filter(
                created_at__date=today
            ).aggregate(
                total_files=Count('id'),
                total_records=Sum('records_total'),
            )
            files   = agg['total_files'] or 0
            records = agg['total_records'] or 0
            failed  = CDRFile.objects.filter(created_at__date=today, status='FAILED').count()
            success = f'{100 * (files - failed) / files:.0f}%' if files > 0 else '—'
            print(f'Files Today: {num(files)}  |  Records: {num(records)}  |  Success: {success}')
        except Exception:
            print(f'{DIM}CDR stats unavailable{N}')
    print()

# ── HEALTH command ─────────────────────────────────────────────────────────────
def show_health() -> None:
    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP SYSTEM HEALTH', ts)

    # SYSTEM
    section('SYSTEM')
    hostname = _run(['hostname']) or '—'
    os_name  = _os_name()
    uptime_s = _sys_uptime()
    load     = _load_avg()
    print(f'  {lc("Hostname", 22)}{hostname}')
    print(f'  {lc("OS", 22)}{os_name}')
    print(f'  {lc("Uptime", 22)}{uptime_s}')
    print(f'  {lc("Load Average", 22)}{load}')
    print()

    # CPU
    cpu_pct, cores = _cpu_info()
    section('CPU')
    if cpu_pct < 70:   cc, cl = G, 'NORMAL'
    elif cpu_pct < 90: cc, cl = Y, 'HIGH'
    else:              cc, cl = R, 'CRITICAL'
    print(f'  {lc("Usage", 22)}{cc}{cpu_pct:.1f}%{N}')
    print(f'  {lc("Cores", 22)}{cores}')
    print(f'  {lc("Load Level", 22)}{cc}{cl}{N}')
    print()

    # MEMORY
    mem = _mem_info()
    section('MEMORY')
    mp = mem['used_pct']
    mc = G if mp < 70 else (Y if mp < 90 else R)
    print(f'  {lc("Total", 22)}{mem["total_gb"]:.1f} GB')
    print(f'  {lc("Used", 22)}{mc}{mem["used_gb"]:.1f} GB{N}')
    print(f'  {lc("Available", 22)}{mem["avail_gb"]:.1f} GB')
    print(f'  {lc("Usage", 22)}{mc}{mp:.1f}%{N}')
    print()

    # STORAGE
    section('STORAGE')
    print(f'  {lc(B+"Mount"+N, 26)}{lc(B+"Used"+N, 12)}{lc(B+"Free"+N, 12)}{B}Usage%{N}')
    print('  ' + '·' * (STA_W - 2))
    disk_warn = False
    for m in _disk_info():
        p = m['pct']
        if p >= 90:   pc = R; disk_warn = True
        elif p >= 75: pc = Y
        else:         pc = G
        print(f'  {lc(m["mount"], 25)}{lc(m["used"], 11)}{lc(m["free"], 11)}{pc}{p}%{N}')
    print()

    # PROCESSES
    section('PROCESSES')
    print(f'  {lc("UMP Processes", 22)}{_pcount("/opt/ump")}')
    print(f'  {lc("Python Processes", 22)}{_pcount("python3")}')
    print(f'  {lc("Worker Processes", 22)}{_pcount("gunicorn")}')
    print()

    # NETWORK
    section('NETWORK')
    rx, tx = _net_rates()
    print(f'  {lc("RX", 22)}{rx:.2f} MB/s')
    print(f'  {lc("TX", 22)}{tx:.2f} MB/s')
    print()

    cpu_ok = cpu_pct < 90
    mem_ok = mp < 90
    if cpu_ok and mem_ok and not disk_warn:
        overall = f'{G}● HEALTHY  {N}'
    elif not cpu_ok or not mem_ok:
        overall = f'{R}● CRITICAL {N}'
    else:
        overall = f'{Y}⚠ DEGRADED {N}'
    print(f' {lc(B+"OVERALL"+N, 8)} {overall}\n')


def _os_name() -> str:
    try:
        with open('/etc/os-release') as f:
            for line in f:
                if line.startswith('PRETTY_NAME='):
                    return line.split('=', 1)[1].strip().strip('"')
    except Exception:
        pass
    return _run(['uname', '-s']) or '—'

def _sys_uptime() -> str:
    try:
        with open('/proc/uptime') as f:
            secs = float(f.read().split()[0])
        d, r = divmod(int(secs), 86400); h, r = divmod(r, 3600); m = r // 60
        parts = []
        if d: parts.append(f'{d} day{"s" if d != 1 else ""}')
        if h: parts.append(f'{h}h')
        if m: parts.append(f'{m}m')
        return ', '.join(parts) or '0m'
    except Exception:
        return '—'

def _load_avg() -> str:
    try:
        with open('/proc/loadavg') as f:
            p = f.read().split()[:3]
        return '  '.join(p)
    except Exception:
        return '—'

def _cpu_info():
    def read():
        try:
            with open('/proc/stat') as f:
                v = list(map(int, f.readline().split()[1:8]))
            return v[3], sum(v)
        except Exception:
            return 0, 1
    i1, t1 = read(); time.sleep(0.5); i2, t2 = read()
    pct = 100.0 * (1 - (i2 - i1) / max(t2 - t1, 1))
    try:
        with open('/proc/cpuinfo') as f:
            cores = f.read().count('processor\t:')
    except Exception:
        cores = 1
    return pct, cores

def _mem_info() -> dict:
    m = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':', 1)
                m[k.strip()] = int(v.split()[0])
    except Exception:
        return {'total_gb': 0.0, 'used_gb': 0.0, 'avail_gb': 0.0, 'used_pct': 0.0}
    total = m.get('MemTotal', 1); avail = m.get('MemAvailable', 0); used = total - avail
    return {
        'total_gb': total / 1048576,
        'used_gb':  used  / 1048576,
        'avail_gb': avail / 1048576,
        'used_pct': 100.0 * used / max(total, 1),
    }

def _disk_info() -> list:
    candidates = ['/', '/ump/landing', '/ump/archive', '/ump/output', '/opt/ump']
    seen = set(); results = []
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            out = subprocess.check_output(['df', '-h', path], text=True, stderr=subprocess.DEVNULL)
            parts = out.strip().split('\n')[1].split()
            mount = parts[5] if len(parts) >= 6 else path
            if mount in seen:
                continue
            seen.add(mount)
            pct_s = parts[4].rstrip('%') if len(parts) >= 5 else '0'
            results.append({'mount': path, 'used': parts[2], 'free': parts[3], 'pct': int(pct_s)})
        except Exception:
            continue
    return results or [{'mount': '/', 'used': '?', 'free': '?', 'pct': 0}]

def _pcount(pattern: str) -> int:
    try:
        out = subprocess.check_output(['pgrep', '-fa', pattern], text=True, stderr=subprocess.DEVNULL)
        return len([l for l in out.strip().split('\n') if l.strip()])
    except subprocess.CalledProcessError:
        return 0
    except Exception:
        return 0

def _net_rates():
    IFACE = re.compile(r'^\s*(\w+):')
    def read():
        s = {}
        try:
            with open('/proc/net/dev') as f:
                for line in f:
                    m = IFACE.match(line)
                    if m and m.group(1) != 'lo':
                        p = line.split(':')[1].split()
                        s[m.group(1)] = (int(p[0]), int(p[8]))
        except Exception:
            pass
        return s
    s1 = read(); time.sleep(1); s2 = read()
    rx = tx = 0
    for iface in s2:
        if iface in s1:
            rx += s2[iface][0] - s1[iface][0]
            tx += s2[iface][1] - s1[iface][1]
    return rx / 1048576, tx / 1048576

# ── CDR command ────────────────────────────────────────────────────────────────
# Column widths: Portal Stream Collected Processed Distributed Pending Error Duplicate
_CW = [14, 8, 12, 12, 14, 10, 8, 11]
_CH = ['Portal', 'Stream', 'Collected', 'Processed', 'Distributed', 'Pending', 'Error', 'Duplicate']

def _cdr_row(*vals) -> str:
    return '  ' + ' '.join(lc(str(v), _CW[i]) for i, v in enumerate(vals))

def show_cdr() -> None:
    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP CDR PIPELINE STATUS', ts, W=CDR_W)
    section('PORTAL / STREAM STATUS', W=CDR_W)

    # header
    hdr_vals = [B + h + N for h in _CH]
    print('  ' + ' '.join(lc(h, _CW[i]) for i, h in enumerate(hdr_vals)))
    print('  ' + '·' * (CDR_W - 2))

    if not _db_ready:
        print(f'  {R}Database not available{N}\n')
        return

    try:
        from django.db.models import Count, Q
        from collection.models import CDRFile

        _processed_statuses = ['DECODED', 'DISPATCHING', 'COMPLETED', 'FAILED',
                                'DUPLICATE', 'EMPTY', 'STAGED', 'PUBLISHED']
        _pending_statuses   = ['COLLECTED', 'PENDING', 'PROCESSING']

        rows = list(
            CDRFile.objects
            .values('source__name', 'decoder_type')
            .annotate(
                collected   = Count('id'),
                processed   = Count('id', filter=Q(status__in=_processed_statuses)),
                distributed = Count('id', filter=Q(status='COMPLETED')),
                pending     = Count('id', filter=Q(status__in=_pending_statuses)),
                error       = Count('id', filter=Q(status='FAILED')),
                duplicate   = Count('id', filter=Q(status='DUPLICATE')),
            )
            .order_by('source__name', 'decoder_type')
        )

        tot = {k: 0 for k in ('collected', 'processed', 'distributed', 'pending', 'error', 'duplicate')}

        for r in rows:
            portal = r['source__name'] or '(unknown)'
            stream = r['decoder_type'] or '—'
            c, p, d = r['collected'], r['processed'], r['distributed']
            pend, e, dup = r['pending'], r['error'], r['duplicate']

            e_s    = f'{R}{num(e)}{N}'    if e    > 0 else num(e)
            pend_s = f'{Y}{num(pend)}{N}' if pend > 0 else num(pend)

            print('  ' + ' '.join([
                lc(portal,    _CW[0]),
                lc(stream,    _CW[1]),
                lc(num(c),    _CW[2]),
                lc(num(p),    _CW[3]),
                lc(num(d),    _CW[4]),
                lc(pend_s,    _CW[5]),
                lc(e_s,       _CW[6]),
                num(dup),
            ]))
            for k in tot:
                tot[k] += r[k]

        if not rows:
            print(f'  {DIM}No CDR files found{N}')
        else:
            print('  ' + '·' * (CDR_W - 2))
            te = f'{R}{num(tot["error"])}{N}'    if tot['error']   > 0 else num(tot['error'])
            tp = f'{Y}{num(tot["pending"])}{N}'  if tot['pending'] > 0 else num(tot['pending'])
            print('  ' + ' '.join([
                lc(B+'TOTAL'+N,              _CW[0]),
                lc('',                       _CW[1]),
                lc(num(tot['collected']),    _CW[2]),
                lc(num(tot['processed']),    _CW[3]),
                lc(num(tot['distributed']),  _CW[4]),
                lc(tp,                       _CW[5]),
                lc(te,                       _CW[6]),
                num(tot['duplicate']),
            ]))

    except Exception as exc:
        print(f'  {R}Query failed: {exc}{N}')

    print()

# ── SERVICE CONTROL ────────────────────────────────────────────────────────────
def _systemctl(action: str, services: list) -> None:
    for svc in services:
        print(f'  {action.capitalize()}ing {svc}... ', end='', flush=True)
        try:
            subprocess.run(['sudo', 'systemctl', action, svc], check=True, capture_output=True)
            print(f'{G}done{N}')
        except subprocess.CalledProcessError:
            print(f'{R}FAILED{N}')

def control_services(action: str, target: str = None) -> None:
    if target:
        full = SERVICE_ALIASES.get(target)
        if not full:
            print(f'{R}Unknown service: {target}{N}')
            print(f'  Valid: api, collector, decoder, distributor')
            sys.exit(1)
        services = [full]
    else:
        services = MEDIATION_SERVICES

    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header(f'UMP SERVICE CONTROL — {action.upper()}', ts)
    _systemctl(action, services)
    print(); time.sleep(1)
    for svc in services:
        state = svc_active(svc)
        print(f'  {lc(svc, 30)}{status_badge(state)}')
    print()

# ── CLEAN command ─────────────────────────────────────────────────────────────
def show_clean() -> None:
    if not _db_ready:
        print(f'{R}Database not available{N}')
        sys.exit(1)

    from collection.models import CDRFile, DistributionLog
    from core.models import ActivityLog
    from django.db.models import Count

    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP DATA CLEANUP', ts)

    # Show current state
    section('CURRENT STATE')
    total = CDRFile.objects.count()
    by_status = {r['status']: r['c'] for r in CDRFile.objects.values('status').annotate(c=Count('id'))}
    dist_count = DistributionLog.objects.count()
    act_count = ActivityLog.objects.count()

    print(f'  CDR Files:         {B}{num(total)}{N}', end='')
    if by_status:
        parts = [f'{s}: {num(c)}' for s, c in sorted(by_status.items())]
        print(f'  ({", ".join(parts)})')
    else:
        print()
    print(f'  Distribution Logs: {B}{num(dist_count)}{N}')
    print(f'  Activity Logs:     {B}{num(act_count)}{N}')
    print()

    # Menu
    section('CLEAN OPTIONS')
    print(f'  {B}[1]{N} Everything — DB + disk files (fresh start)')
    print(f'  {B}[2]{N} CDR Files + Distribution Logs only')
    print(f'  {B}[3]{N} Failed files only (remove from DB, re-enable reprocessing)')
    print(f'  {B}[4]{N} Activity Logs only')
    print(f'  {B}[5]{N} Disk files only (keep DB records)')
    print(f'  {B}[0]{N} Cancel')
    print()

    try:
        choice = input(f'  Choice [0-5]: ').strip()
    except (KeyboardInterrupt, EOFError):
        print('\n  Cancelled.')
        return

    if choice == '0' or not choice:
        print('  Cancelled.')
        return

    if choice not in ('1', '2', '3', '4', '5'):
        print(f'  {R}Invalid choice{N}')
        return

    # Confirm
    try:
        confirm = input(f'\n  {Y}⚠ This will stop the collector first. Continue? [y/N]:{N} ').strip().lower()
    except (KeyboardInterrupt, EOFError):
        print('\n  Cancelled.')
        return

    if confirm != 'y':
        print('  Cancelled.')
        return

    # Stop collector
    print(f'\n  Stopping collector...', end=' ', flush=True)
    subprocess.run(['sudo', 'systemctl', 'stop', 'mediation-collector'], capture_output=True)
    print(f'{G}done{N}')

    deleted = {}

    if choice in ('1', '2'):
        c = CDRFile.objects.count()
        CDRFile.objects.all().delete()
        deleted['CDR Files'] = c
        d = DistributionLog.objects.count()
        DistributionLog.objects.all().delete()
        deleted['Distribution Logs'] = d

    if choice == '1':
        a = ActivityLog.objects.count()
        ActivityLog.objects.all().delete()
        deleted['Activity Logs'] = a

    if choice == '3':
        c = CDRFile.objects.filter(status='FAILED').count()
        CDRFile.objects.filter(status='FAILED').delete()
        deleted['Failed CDR Files'] = c

    if choice == '4':
        a = ActivityLog.objects.count()
        ActivityLog.objects.all().delete()
        deleted['Activity Logs'] = a

    # Disk cleanup
    if choice in ('1', '5'):
        data_root = os.environ.get('UMP_STORAGE_ROOT', '/opt/ump/data')
        dirs_to_clean = ['landing', 'processing', 'error', 'quarantine']
        file_count = 0
        for d in dirs_to_clean:
            full = os.path.join(data_root, d)
            if os.path.isdir(full):
                for root, dirs, files in os.walk(full):
                    for f in files:
                        os.remove(os.path.join(root, f))
                        file_count += 1
        deleted['Disk files'] = file_count

    # Report
    print()
    section('CLEANUP RESULTS')
    for k, v in deleted.items():
        print(f'  {lc(k, 25)} {G}{num(v)} deleted{N}')

    # Restart collector
    print(f'\n  Restarting collector...', end=' ', flush=True)
    subprocess.run(['sudo', 'systemctl', 'start', 'mediation-collector'], capture_output=True)
    print(f'{G}done{N}')
    print()


# ── STATS command ─────────────────────────────────────────────────────────────
def show_stats() -> None:
    if not _db_ready:
        print(f'{R}Database not available{N}')
        return

    from django.db.models import Sum, Count, Avg, Q
    from django.utils import timezone
    from collection.models import CDRFile

    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP CDR STATISTICS', ts)

    today = timezone.now().date()

    # Overall
    section('OVERALL')
    total = CDRFile.objects.count()
    agg = CDRFile.objects.aggregate(
        records=Sum('records_total'),
        completed=Count('id', filter=Q(status='COMPLETED')),
        failed=Count('id', filter=Q(status='FAILED')),
        pending=Count('id', filter=Q(status__in=['COLLECTED', 'PENDING', 'PROCESSING'])),
    )
    rate = f'{100 * agg["completed"] / total:.1f}%' if total > 0 else '—'
    print(f'  {lc("Total Files", 22)}{num(total)}')
    print(f'  {lc("Total Records", 22)}{num(agg["records"] or 0)}')
    print(f'  {lc("Completed", 22)}{G}{num(agg["completed"])}{N}')
    print(f'  {lc("Failed", 22)}{R}{num(agg["failed"])}{N}' if agg['failed'] else f'  {lc("Failed", 22)}0')
    print(f'  {lc("Pending", 22)}{Y}{num(agg["pending"])}{N}' if agg['pending'] else f'  {lc("Pending", 22)}0')
    print(f'  {lc("Success Rate", 22)}{rate}')
    print()

    # By Operator
    section('BY OPERATOR')
    print(f'  {lc(B+"Operator"+N, 24)}{lc(B+"Files"+N, 12)}{lc(B+"Records"+N, 14)}{lc(B+"Failed"+N, 10)}{B}Rate{N}')
    print('  ' + '·' * (STA_W - 2))
    for r in CDRFile.objects.values('operator_code').annotate(
        files=Count('id'), records=Sum('records_total'),
        completed=Count('id', filter=Q(status='COMPLETED')),
        failed=Count('id', filter=Q(status='FAILED')),
    ).order_by('operator_code'):
        op = r['operator_code'] or '(unknown)'
        rt = f'{100 * r["completed"] / r["files"]:.0f}%' if r['files'] > 0 else '—'
        f_s = f'{R}{num(r["failed"])}{N}' if r['failed'] else '0'
        print(f'  {lc(op, 22)}{lc(num(r["files"]), 12)}{lc(num(r["records"] or 0), 14)}{lc(f_s, 10)}{rt}')
    print()

    # By Stream
    section('BY STREAM')
    print(f'  {lc(B+"Stream"+N, 24)}{lc(B+"Files"+N, 12)}{lc(B+"Records"+N, 14)}{lc(B+"Failed"+N, 10)}{B}Rate{N}')
    print('  ' + '·' * (STA_W - 2))
    for r in CDRFile.objects.values('decoder_type').annotate(
        files=Count('id'), records=Sum('records_total'),
        completed=Count('id', filter=Q(status='COMPLETED')),
        failed=Count('id', filter=Q(status='FAILED')),
    ).order_by('decoder_type'):
        st = r['decoder_type'] or '(unknown)'
        rt = f'{100 * r["completed"] / r["files"]:.0f}%' if r['files'] > 0 else '—'
        f_s = f'{R}{num(r["failed"])}{N}' if r['failed'] else '0'
        print(f'  {lc(st, 22)}{lc(num(r["files"]), 12)}{lc(num(r["records"] or 0), 14)}{lc(f_s, 10)}{rt}')
    print()

    # Today
    section('TODAY')
    t_agg = CDRFile.objects.filter(created_at__date=today).aggregate(
        files=Count('id'), records=Sum('records_total'),
        completed=Count('id', filter=Q(status='COMPLETED')),
        failed=Count('id', filter=Q(status='FAILED')),
    )
    t_rate = f'{100 * t_agg["completed"] / t_agg["files"]:.0f}%' if t_agg['files'] else '—'
    print(f'  {lc("Files Today", 22)}{num(t_agg["files"])}')
    print(f'  {lc("Records Today", 22)}{num(t_agg["records"] or 0)}')
    print(f'  {lc("Success Rate", 22)}{t_rate}')
    print()


# ── IP command ────────────────────────────────────────────────────────────────
def change_ip() -> None:
    new_ip = sys.argv[2] if len(sys.argv) > 2 else None

    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP IP ADDRESS UPDATE', ts)

    # Detect current IP
    env_file = '/opt/ump/.env.systemd'
    nginx_conf = '/etc/nginx/sites-available/ump'

    current_ip = None
    if os.path.exists(env_file):
        try:
            with open(env_file) as f:
                for line in f:
                    if line.startswith('DJANGO_ALLOWED_HOSTS='):
                        hosts = line.split('=', 1)[1].strip().strip('"').strip("'")
                        for h in hosts.split(','):
                            h = h.strip()
                            if h and h not in ('localhost', '127.0.0.1', '*'):
                                current_ip = h
                                break
        except Exception:
            pass

    if current_ip:
        print(f'  Current IP: {B}{current_ip}{N}')
    else:
        print(f'  {Y}Could not detect current IP from {env_file}{N}')

    # Get actual IP from system
    actual_ip = _run(['hostname', '-I']).split()[0] if _run(['hostname', '-I']) else None
    if actual_ip:
        print(f'  System IP:  {B}{actual_ip}{N}')

    if not new_ip:
        if actual_ip and actual_ip != current_ip:
            new_ip = actual_ip
            print(f'\n  {Y}IP change detected: {current_ip} → {actual_ip}{N}')
        else:
            try:
                new_ip = input(f'\n  Enter new IP address: ').strip()
            except (KeyboardInterrupt, EOFError):
                print('\n  Cancelled.')
                return

    if not new_ip or not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', new_ip):
        print(f'  {R}Invalid IP address: {new_ip}{N}')
        return

    if new_ip == current_ip:
        print(f'  {G}IP already set to {new_ip}, no changes needed.{N}')
        return

    print(f'\n  Updating: {current_ip or "?"} → {B}{new_ip}{N}')

    # Update .env.systemd
    if current_ip and os.path.exists(env_file):
        print(f'  Updating {env_file}...', end=' ', flush=True)
        subprocess.run(['sudo', 'sed', '-i', f's/{current_ip}/{new_ip}/g', env_file],
                       capture_output=True)
        print(f'{G}done{N}')
    else:
        print(f'  {Y}Skipped {env_file} — current IP unknown, update manually{N}')

    # Update nginx
    if current_ip and os.path.exists(nginx_conf):
        print(f'  Updating {nginx_conf}...', end=' ', flush=True)
        subprocess.run(['sudo', 'sed', '-i', f's/{current_ip}/{new_ip}/g', nginx_conf],
                       capture_output=True)
        print(f'{G}done{N}')

    # Also update .env if it exists
    env_cli = '/opt/ump/.env'
    if current_ip and os.path.exists(env_cli):
        subprocess.run(['sudo', 'sed', '-i', f's/{current_ip}/{new_ip}/g', env_cli],
                       capture_output=True)

    # Reload
    print(f'\n  Testing nginx config...', end=' ', flush=True)
    r = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
    if r.returncode == 0:
        print(f'{G}OK{N}')
        print(f'  Reloading nginx...', end=' ', flush=True)
        subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], capture_output=True)
        print(f'{G}done{N}')
        print(f'  Restarting API...', end=' ', flush=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'mediation-api'], capture_output=True)
        print(f'{G}done{N}')
    else:
        print(f'{R}FAILED{N}')
        print(f'  {R}Nginx config test failed — check manually{N}')
        print(f'  {r.stderr}')

    print(f'\n  {G}✓ IP updated to {new_ip}{N}')
    print(f'  Access: http://{new_ip}/\n')


# ── PORTALS command ───────────────────────────────────────────────────────────
def show_portals() -> None:
    if not _db_ready:
        print(f'{R}Database not available{N}')
        return

    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP PORTAL CONFIGURATION', ts)

    # Input Sources
    section('INPUT SOURCES (DataSource)')
    try:
        from collection.models import DataSource
        sources = DataSource.objects.all()
        if not sources.exists():
            print(f'  {DIM}No data sources configured{N}')
        else:
            print(f'  {lc(B+"Name"+N, 22)}{lc(B+"Type"+N, 16)}{lc(B+"Enabled"+N, 10)}{B}Path / Host{N}')
            print('  ' + '·' * (STA_W - 2))
            for s in sources:
                en = f'{G}Yes{N}' if s.enabled else f'{R}No{N}'
                loc = s.sftp_host or s.local_path or '—'
                print(f'  {lc(s.name, 20)}{lc(s.get_source_type_display(), 16)}{lc(en, 10)}{loc}')
    except Exception as exc:
        print(f'  {R}Error: {exc}{N}')
    print()

    # Output Portals
    section('OUTPUT PORTALS')
    try:
        from portals.models import OutputPortal
        portals = OutputPortal.objects.all()
        if not portals.exists():
            print(f'  {DIM}No output portals configured{N}')
        else:
            data_root = os.environ.get('UMP_STORAGE_ROOT', '/opt/ump/data')
            print(f'  {lc(B+"Name"+N, 22)}{lc(B+"Format"+N, 10)}{lc(B+"Valid"+N, 10)}{B}Directory{N}')
            print('  ' + '·' * (STA_W - 2))
            for p in portals:
                fmt = getattr(p, 'output_format', '—') or '—'
                d = p.directory or '—'
                valid = d.startswith(data_root) or '{' in d
                v_s = f'{G}Yes{N}' if valid else f'{R}No{N}'
                print(f'  {lc(p.name, 20)}{lc(fmt, 10)}{lc(v_s, 10)}{d}')
    except Exception as exc:
        print(f'  {R}Error: {exc}{N}')
    print()

    # Distribution Rules
    section('DISTRIBUTION RULES')
    try:
        from portals.models import DistributionRule
        rules = DistributionRule.objects.select_related('output_portal').all()
        if not rules.exists():
            print(f'  {DIM}No distribution rules configured{N}')
        else:
            print(f'  {lc(B+"Rule"+N, 22)}{lc(B+"Stream"+N, 10)}{lc(B+"Portal"+N, 18)}{B}Active{N}')
            print('  ' + '·' * (STA_W - 2))
            for r in rules:
                portal = r.output_portal.name if r.output_portal else '—'
                act = f'{G}Yes{N}' if r.is_active else f'{R}No{N}'
                print(f'  {lc(r.name, 20)}{lc(r.stream_type or "ALL", 10)}{lc(portal, 18)}{act}')
    except Exception as exc:
        print(f'  {R}Error: {exc}{N}')
    print()


# ── DIRS command ──────────────────────────────────────────────────────────────
def show_dirs() -> None:
    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP DIRECTORY STATUS', ts)

    data_root = os.environ.get('UMP_STORAGE_ROOT', '/opt/ump/data')
    print(f'  Storage Root: {B}{data_root}{N}\n')

    categories = [
        ('landing/input',  'Input (collection landing)'),
        ('landing/output', 'Output (distribution)'),
        ('processing',     'Processing (active decode)'),
        ('archive',        'Archive'),
        ('error',          'Error'),
        ('quarantine',     'Quarantine'),
    ]

    for subdir, label in categories:
        full = os.path.join(data_root, subdir)
        if os.path.isdir(full):
            dir_count = sum(1 for _, dd, _ in os.walk(full) for _ in dd)
            file_count = sum(1 for _, _, ff in os.walk(full) for _ in ff)
            total_size = sum(
                os.path.getsize(os.path.join(r, f))
                for r, _, ff in os.walk(full) for f in ff
            )
            size_str = _human_size(total_size)
            status = f'{G}OK{N}'
        else:
            dir_count = file_count = 0
            size_str = '0 B'
            status = f'{R}MISSING{N}'

        print(f'  {lc(label, 30)}{status}  {lc(f"{file_count} files", 14)}{size_str}')

    print()


def _human_size(size_bytes: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size_bytes < 1024:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.1f} PB'


# ── DISK command ──────────────────────────────────────────────────────────────
def show_disk() -> None:
    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP STORAGE USAGE', ts)

    data_root = os.environ.get('UMP_STORAGE_ROOT', '/opt/ump/data')
    print(f'  Storage Root: {B}{data_root}{N}\n')

    section('DIRECTORY BREAKDOWN')
    print(f'  {lc(B+"Directory"+N, 35)}{lc(B+"Files"+N, 10)}{lc(B+"Size"+N, 12)}{B}Oldest File{N}')
    print('  ' + '·' * (STA_W - 2))

    for root, dirs, files_in_root in sorted(os.walk(data_root)):
        depth = root.replace(data_root, '').count(os.sep)
        if depth > 2:
            continue
        rel = os.path.relpath(root, data_root)
        if rel == '.':
            rel = '(root)'

        file_count = 0
        total_size = 0
        oldest = None
        for r, _, ff in os.walk(root):
            for f in ff:
                fp = os.path.join(r, f)
                try:
                    st = os.stat(fp)
                    file_count += 1
                    total_size += st.st_size
                    mt = datetime.datetime.fromtimestamp(st.st_mtime)
                    if oldest is None or mt < oldest:
                        oldest = mt
                except OSError:
                    pass

        if depth == 0 and rel == '(root)':
            continue

        oldest_s = oldest.strftime('%Y-%m-%d %H:%M') if oldest else '—'
        indent = '  ' * min(depth, 1)
        print(f'  {lc(indent + rel, 33)}{lc(str(file_count), 10)}{lc(_human_size(total_size), 12)}{oldest_s}')

    print()


# ── LOGS command ──────────────────────────────────────────────────────────────
def show_logs() -> None:
    target = sys.argv[2] if len(sys.argv) > 2 else None
    tail_n = '50'
    if len(sys.argv) > 3 and sys.argv[3] in ('--tail', '-n') and len(sys.argv) > 4:
        tail_n = sys.argv[4]
    elif len(sys.argv) > 3:
        tail_n = sys.argv[3]

    if target:
        full = SERVICE_ALIASES.get(target)
        if not full:
            print(f'{R}Unknown service: {target}{N}')
            print(f'  Valid: api, collector, decoder, distributor')
            sys.exit(1)
        unit = full
    else:
        unit = 'mediation-*'

    cmd = ['sudo', 'journalctl', '-u', unit, '--no-pager', '-n', str(tail_n)]
    os.execvp('sudo', cmd)


# ── CHECK command ─────────────────────────────────────────────────────────────
def show_check() -> None:
    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP SYSTEM CHECK', ts)

    checks_passed = 0
    checks_failed = 0

    def check(label: str, ok: bool, detail: str = ''):
        nonlocal checks_passed, checks_failed
        if ok:
            checks_passed += 1
            print(f'  {G}✓{N} {lc(label, 35)}{detail}')
        else:
            checks_failed += 1
            print(f'  {R}✗{N} {lc(label, 35)}{R}{detail}{N}')

    # 1. Env vars loaded
    section('ENVIRONMENT')
    db_engine = ''
    if _db_ready:
        from django.conf import settings as djsettings
        db_engine = djsettings.DATABASES.get('default', {}).get('ENGINE', '')

    check('Django loaded', _db_ready)
    check('Using PostgreSQL (not SQLite)',
          'postgresql' in db_engine,
          db_engine.split('.')[-1] if db_engine else 'not loaded')

    if _db_ready:
        check('DEBUG is False',
              not djsettings.DEBUG,
              f'DEBUG={djsettings.DEBUG}')
        check('SECRET_KEY is set',
              djsettings.SECRET_KEY and 'changeme' not in djsettings.SECRET_KEY.lower(),
              'configured' if djsettings.SECRET_KEY else 'MISSING')
    print()

    # 2. Services
    section('SERVICES')
    for svc in ALL_SERVICES:
        state = svc_active(svc)
        check(svc, state == 'active', state)
    print()

    # 3. Database
    section('DATABASE')
    db = _db_status()
    check('Database connection', db['ok'],
          f'{db.get("dbname", "?")} ({db.get("ms", "?")}ms)' if db['ok'] else db.get('error', ''))

    if _db_ready:
        try:
            from django.db import connections
            for alias in ['default', 'mediation_orange', 'mediation_africell', 'mediation_qcell']:
                try:
                    conn = connections[alias]
                    conn.ensure_connection()
                    check(f'Database: {alias}', True, conn.settings_dict.get('NAME', ''))
                except Exception as e:
                    check(f'Database: {alias}', False, str(e)[:60])
        except Exception:
            pass
    print()

    # 4. Directories
    section('STORAGE')
    data_root = os.environ.get('UMP_STORAGE_ROOT', '/opt/ump/data')
    check('UMP_STORAGE_ROOT exists', os.path.isdir(data_root), data_root)
    for sub in ['landing/input', 'landing/output', 'processing', 'error', 'quarantine']:
        full = os.path.join(data_root, sub)
        check(f'  {sub}/', os.path.isdir(full))
    print()

    # 5. Portal paths
    if _db_ready:
        section('PORTAL PATHS')
        try:
            from portals.models import OutputPortal
            for p in OutputPortal.objects.all():
                valid = p.directory.startswith(data_root) or '{' in p.directory
                check(p.name, valid, p.directory)
        except Exception:
            print(f'  {DIM}Could not check portals{N}')
        print()

    # 6. Static files
    section('WEB')
    static_dir = '/opt/ump/mediation/staticfiles'
    check('Static files directory', os.path.isdir(static_dir))

    # Nginx
    r = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
    check('Nginx config valid', r.returncode == 0)

    # Health endpoint
    health = _run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', 'http://localhost/health/'])
    check('Health endpoint', health == '200', f'HTTP {health}')
    print()

    # Summary
    total = checks_passed + checks_failed
    if checks_failed == 0:
        print(f'  {G}✓ All {total} checks passed{N}\n')
    else:
        print(f'  {R}✗ {checks_failed} of {total} checks failed{N}\n')


# ── BACKUP command ────────────────────────────────────────────────────────────
def run_backup() -> None:
    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP DATABASE BACKUP', ts)

    backup_dir = '/opt/ump/backups'
    os.makedirs(backup_dir, exist_ok=True)

    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    databases = ['ump_mediation', 'mediation_orange', 'mediation_africell', 'mediation_qcell']

    section('DATABASE DUMPS')
    for db in databases:
        filename = f'{db}_{stamp}.sql.gz'
        filepath = os.path.join(backup_dir, filename)
        print(f'  Dumping {db}...', end=' ', flush=True)
        cmd = f'pg_dump -U ump {db} | gzip > {filepath}'
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(filepath):
            size = _human_size(os.path.getsize(filepath))
            print(f'{G}done{N} ({size})')
        else:
            print(f'{R}FAILED{N}')
            if r.stderr:
                print(f'    {r.stderr[:100]}')
    print()

    # Backup env files
    section('CONFIG FILES')
    for src in ['/opt/ump/.env', '/opt/ump/.env.systemd']:
        if os.path.exists(src):
            dst = os.path.join(backup_dir, f'{os.path.basename(src)}_{stamp}')
            shutil.copy2(src, dst)
            print(f'  {G}✓{N} {src} → {dst}')
    print()

    # Backup nginx
    nginx_src = '/etc/nginx/sites-available/ump'
    if os.path.exists(nginx_src):
        dst = os.path.join(backup_dir, f'nginx_ump_{stamp}')
        subprocess.run(['sudo', 'cp', nginx_src, dst], capture_output=True)
        print(f'  {G}✓{N} {nginx_src} → {dst}')
    print()

    print(f'  Backup location: {B}{backup_dir}{N}\n')


# ── DEPLOY command ────────────────────────────────────────────────────────────
def run_deploy() -> None:
    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP DEPLOYMENT', ts)

    print(f'  {Y}This will:{N}')
    print(f'  1. Stop all mediation services')
    print(f'  2. Run database migrations')
    print(f'  3. Collect static files')
    print(f'  4. Fix permissions')
    print(f'  5. Run system check')
    print(f'  6. Start all services')
    print()

    try:
        confirm = input(f'  {Y}Proceed? [y/N]:{N} ').strip().lower()
    except (KeyboardInterrupt, EOFError):
        print('\n  Cancelled.')
        return

    if confirm != 'y':
        print('  Cancelled.')
        return

    steps = [
        ('Stopping services',
         ['sudo', 'systemctl', 'stop'] + MEDIATION_SERVICES),
        ('Running migrations',
         ['/opt/ump/venv/bin/python', '/opt/ump/mediation/manage.py', 'migrate', '--noinput']),
        ('Collecting static files',
         ['/opt/ump/venv/bin/python', '/opt/ump/mediation/manage.py', 'collectstatic', '--noinput']),
        ('Fixing permissions',
         ['sudo', 'chmod', '-R', '755', '/opt/ump/mediation/staticfiles']),
    ]

    for label, cmd in steps:
        print(f'\n  {label}...', end=' ', flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            print(f'{G}done{N}')
        else:
            print(f'{R}FAILED{N}')
            if r.stderr:
                print(f'    {r.stderr[:200]}')

    # System check
    print(f'\n  Running system check...', end=' ', flush=True)
    r = subprocess.run(
        ['/opt/ump/venv/bin/python', '/opt/ump/mediation/manage.py', 'check'],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        print(f'{G}OK{N}')
    else:
        print(f'{Y}warnings{N}')
        if r.stdout:
            for line in r.stdout.strip().split('\n')[-5:]:
                print(f'    {line}')

    # Start services
    print(f'\n  Starting services...', end=' ', flush=True)
    subprocess.run(['sudo', 'systemctl', 'start'] + MEDIATION_SERVICES, capture_output=True)
    print(f'{G}done{N}')

    time.sleep(2)
    print(f'\n  Service status:')
    for svc in MEDIATION_SERVICES:
        state = svc_active(svc)
        print(f'    {lc(svc, 30)}{status_badge(state)}')

    # Health check
    time.sleep(1)
    health = _run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', 'http://localhost/health/'])
    if health == '200':
        print(f'\n  {G}✓ Deployment complete — system healthy{N}\n')
    else:
        print(f'\n  {Y}⚠ Deployment complete — health check returned HTTP {health}{N}\n')


# ── MAIN ──────────────────────────────────────────────────────────────────────
COMMANDS = {
    'status':  ('Display operations dashboard',        show_status),
    'health':  ('Full system health check',            show_health),
    'cdr':     ('CDR pipeline status by portal/stream', show_cdr),
    'stats':   ('CDR statistics by operator/stream',   show_stats),
    'clean':   ('Interactive data cleanup',            show_clean),
    'ip':      ('Update server IP address',            change_ip),
    'portals': ('Show portal configuration',           show_portals),
    'dirs':    ('Verify directory structure',           show_dirs),
    'disk':    ('Storage usage breakdown',             show_disk),
    'logs':    ('View service logs',                   show_logs),
    'check':   ('Run system diagnostics',              show_check),
    'backup':  ('Backup databases and config',         run_backup),
    'deploy':  ('Run deployment steps',                run_deploy),
}

def main() -> None:
    cmd    = sys.argv[1].lower() if len(sys.argv) > 1 else 'status'
    target = sys.argv[2].lower() if len(sys.argv) > 2 else None

    if cmd in ('restart', 'stop', 'start'):
        control_services(cmd, target)
    elif cmd in COMMANDS:
        COMMANDS[cmd][1]()
    elif cmd in ('help', '--help', '-h'):
        print(f'\n{B}UMP Mediation — Operations CLI{N}\n')
        print(f'Usage: ump <command> [options]\n')
        for name, (desc, _) in COMMANDS.items():
            print(f'  {lc(name, 12)}{desc}')
        print(f'  {"restart":<12}Restart service (api|collector|decoder|distributor)')
        print(f'  {"stop":<12}Stop service')
        print(f'  {"start":<12}Start service')
        print()
    else:
        print(f'{R}Unknown command: {cmd}{N}')
        print(f'Run {B}ump help{N} for available commands.')
        sys.exit(1)

if __name__ == '__main__':
    main()
