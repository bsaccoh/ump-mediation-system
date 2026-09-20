#!/opt/ump/venv/bin/python
# UMP Mediation — Operations CLI
# Usage: ump <status|health|cdr|restart|stop|start> [service]

import os, sys, re, subprocess, datetime, time

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
            secs = int((datetime.datetime.utcnow() - dt).total_seconds())
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
    ts = datetime.datetime.now().strftime('%d %b %Y  %H:%M:%S')
    box_header('UMP SYSTEM STATUS', ts)

    section('SERVICES')
    print(f'  {lc(B+"Service"+N, 30)}{lc(B+"Status"+N, 22)}{lc(B+"Uptime"+N, 16)}{B}PID{N}')
    print('  ' + '·' * (STA_W - 2))

    any_down = False
    for svc in ALL_SERVICES:
        state  = svc_active(svc)
        badge  = status_badge(state)
        uptime = svc_uptime(svc) if state == 'active' else '—'
        pid    = svc_pid(svc)    if state == 'active' else '—'
        if state != 'active':
            any_down = True
        print(f'  {lc(svc, 29)}{lc(badge, 27)}{lc(uptime, 16)}{pid}')
    print()

    section('DATABASE')
    db = _db_status()
    if db['ok']:
        print(f'  {lc("PostgreSQL", 22)}{G}● CONNECTED{N}')
        print(f'  {lc("Database", 22)}{db["dbname"]}')
        print(f'  {lc("Active Connections", 22)}{db["connections"]}')
        print(f'  {lc("Response Time", 22)}{db["ms"]} ms')
    else:
        print(f'  {lc("PostgreSQL", 22)}{R}✗ DISCONNECTED{N}')
        if db.get('error'):
            print(f'  {DIM}{db["error"]}{N}')
    print()

    if not any_down and db['ok']:
        overall = f'{G}● OPERATIONAL{N}'
    elif any_down and not db['ok']:
        overall = f'{R}● CRITICAL   {N}'
    else:
        overall = f'{Y}⚠ DEGRADED   {N}'
    print(f' {lc(B+"OVERALL"+N, 8)} {overall}\n')

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

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main() -> None:
    cmd    = sys.argv[1].lower() if len(sys.argv) > 1 else 'status'
    target = sys.argv[2].lower() if len(sys.argv) > 2 else None

    if cmd == 'status':
        show_status()
    elif cmd == 'health':
        show_health()
    elif cmd == 'cdr':
        show_cdr()
    elif cmd in ('restart', 'stop', 'start'):
        control_services(cmd, target)
    else:
        print(f'Usage: ump <status|health|cdr|restart|stop|start> [service]')
        print(f'       ump restart api      — restart a single service')
        sys.exit(1)

if __name__ == '__main__':
    main()
