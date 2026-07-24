"""Launch the parallel `process_batch` decoder as a detached subprocess.

Running it as a fresh process (rather than inline in a web request or a Celery
worker) keeps the multiprocessing decode pool clean and returns immediately.
Both the UI "Run collection now" button and the scheduled Celery task use this.
"""
import os
import subprocess
import sys
from datetime import datetime

from django.conf import settings


def launch_batch(operator: str | None = None, *, reprocess: bool = False,
                 workers: int | None = None) -> dict:
    """Spawn `manage.py process_batch` in the background. Returns {pid, log}."""
    base_dir = str(settings.BASE_DIR)
    log_dir = os.path.join(str(settings.DATA_DIR), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(log_dir, f'process_batch_{operator or "all"}_{stamp}.log')

    cmd = [sys.executable, os.path.join(base_dir, 'manage.py'), 'process_batch']
    if operator:
        cmd += ['--operator', operator]
    if reprocess:
        cmd += ['--reprocess']
    if workers:
        cmd += ['--workers', str(workers)]

    # Detach so the parent (web request / celery task) returns immediately.
    creationflags = 0
    if os.name == 'nt':
        creationflags = getattr(subprocess, 'DETACHED_PROCESS', 0)

    logf = open(log_path, 'w')
    proc = subprocess.Popen(
        cmd, cwd=base_dir, stdout=logf, stderr=subprocess.STDOUT,
        creationflags=creationflags, close_fds=True,
    )
    return {'pid': proc.pid, 'log': log_path, 'operator': operator or 'all'}
