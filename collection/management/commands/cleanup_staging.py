"""Remove orphaned staging files older than a configurable threshold.

When a process crashes after writing to a staging directory but before
os.replace(), the .{name}.{uuid}.part file remains indefinitely.  The
dotfile prefix and .part suffix prevent collectors from picking them up,
but they accumulate disk space over time.

Usage:
    python manage.py cleanup_staging --max-age 3600
    python manage.py cleanup_staging --dry-run
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Remove orphaned .part staging files older than --max-age seconds.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-age', type=int, default=3600,
            help='Minimum file age in seconds before removal (default: 3600).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List files that would be removed without deleting them.',
        )

    def handle(self, **options):
        max_age = options['max_age']
        dry_run = options['dry_run']
        now = time.time()
        removed = 0
        total_bytes = 0

        roots = []
        for attr in ('UMP_INPUT_ROOT', 'UMP_OUTPUT_ROOT', 'UMP_ARCHIVE_ROOT',
                      'UMP_PROCESSING_ROOT', 'UMP_ERROR_ROOT', 'UMP_QUARANTINE_ROOT'):
            root = getattr(settings, attr, None)
            if root:
                roots.append(Path(root))

        for root in roots:
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                if os.path.basename(dirpath) != 'staging':
                    continue
                for fname in filenames:
                    if not fname.endswith('.part'):
                        continue
                    fpath = os.path.join(dirpath, fname)
                    try:
                        age = now - os.path.getmtime(fpath)
                    except OSError:
                        continue
                    if age < max_age:
                        continue
                    size = os.path.getsize(fpath)
                    if dry_run:
                        self.stdout.write(f'[dry-run] {fpath} ({size} bytes, {age:.0f}s old)')
                    else:
                        try:
                            os.unlink(fpath)
                            self.stdout.write(f'Removed {fpath} ({size} bytes, {age:.0f}s old)')
                        except OSError as exc:
                            self.stderr.write(f'Failed to remove {fpath}: {exc}')
                            continue
                    removed += 1
                    total_bytes += size

        action = 'Would remove' if dry_run else 'Removed'
        self.stdout.write(self.style.SUCCESS(
            f'{action} {removed} orphaned staging file(s), {total_bytes:,} bytes total.',
        ))
