"""Delete decoded CDR records (across all operator databases) for testing.

Wipes every stream's records in each operator's DB (home operator -> default,
others -> mediation_{op}) plus the CDRFile tracker. Use --files to also empty
the data directories.

    python manage.py clear_cdr
    python manage.py clear_cdr --operator africell
    python manage.py clear_cdr --files
"""
import importlib
import os
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand

from collection.models import CDRFile
from core.operator_context import operator_context

STREAM_MODELS = [
    ('streams.msc.models', 'MSCRecord'),
    ('streams.ims.models', 'IMSRecord'),
    ('streams.pgw.models', 'PGWRecord'),
    ('streams.sgsn.models', 'SGSNRecord'),
    ('streams.sgw.models', 'SGWRecord'),
    ('streams.cbs.models', 'CBSRecord'),
]


class Command(BaseCommand):
    help = 'Delete all decoded CDR records (per operator) + CDRFile entries.'

    def add_arguments(self, parser):
        parser.add_argument('--operator', help='Only this operator code (default: all).')
        parser.add_argument('--files', action='store_true',
                            help='Also empty the data directories (input/output/etc.).')

    def handle(self, *args, **opts):
        models = []
        for mod, name in STREAM_MODELS:
            try:
                models.append(getattr(importlib.import_module(mod), name))
            except Exception:
                pass

        operators = ([opts['operator'].lower()] if opts.get('operator')
                     else (list(settings.OPERATORS) or [settings.DEFAULT_OPERATOR]))

        for op in operators:
            with operator_context(op):
                for model in models:
                    try:
                        deleted = model.objects.all().delete()[0]
                    except Exception as exc:  # operator DB maybe not provisioned
                        self.stdout.write(self.style.WARNING(
                            f'[{op}] {model.__name__}: skipped ({str(exc).splitlines()[0][:80]})'))
                        continue
                    if deleted:
                        self.stdout.write(f'[{op}] {model.__name__}: deleted {deleted}')

        # CDRFile is control-plane (shared default DB) — delete once.
        file_count = CDRFile.objects.all().delete()[0]
        self.stdout.write(f'CDRFile: deleted {file_count}')

        if opts.get('files'):
            self._clean_dirs()

        self.stdout.write(self.style.SUCCESS('CDR data cleared.'))

    def _clean_dirs(self):
        data_dir = str(settings.DATA_DIR)
        # Legacy flat dirs + per-operator input/output trees.
        targets = ['incoming', 'processed', 'decoded', 'failed', 'archive']
        targets += [op for op in (list(settings.OPERATORS) or [])]
        for name in targets:
            path = os.path.join(data_dir, name)
            if not os.path.isdir(path):
                continue
            for item in os.listdir(path):
                ip = os.path.join(path, item)
                try:
                    shutil.rmtree(ip) if os.path.isdir(ip) else os.unlink(ip)
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f'  could not delete {ip}: {exc}'))
            self.stdout.write(f'cleaned dir: {path}')
