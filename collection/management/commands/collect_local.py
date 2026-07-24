"""Scan the per-operator input tree and register new CDR files.

Walks ``DATA_DIR/{operator}/input/{vendor}/{network_element}/`` and creates a
CDRFile (status PENDING -> signal triggers processing) for every file not yet
collected (deduplicated by content hash). Operator/vendor/network-element are
taken primarily from the filename classification (SourcePattern) and fall back
to the directory segments the file was found under.

    python manage.py collect_local
    python manage.py collect_local --operator orange
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from collection.models import CDRFile
from collection.services.deduplication import get_file_hash, check_duplicate
from collection.services.file_detector import classify_file


class Command(BaseCommand):
    help = 'Scan DATA_DIR/{operator}/input/{vendor}/{ne}/ and register new files.'

    def add_arguments(self, parser):
        parser.add_argument('--operator', help='Limit to one operator code.')

    def handle(self, *args, **opts):
        only = opts.get('operator')
        data_dir = str(settings.DATA_DIR)
        collected = skipped = 0

        for operator in sorted(os.listdir(data_dir)) if os.path.isdir(data_dir) else []:
            if only and operator != only:
                continue
            input_root = os.path.join(data_dir, operator, 'input')
            if not os.path.isdir(input_root):
                continue
            for root, _dirs, files in os.walk(input_root):
                # Path segments after .../input/: vendor / ne / ...
                rel = os.path.relpath(root, input_root).split(os.sep)
                path_vendor = rel[0] if rel and rel[0] != '.' else ''
                path_ne = rel[1] if len(rel) > 1 else ''
                for fname in files:
                    if fname.startswith('.'):
                        continue
                    fpath = os.path.join(root, fname)
                    if check_duplicate(fpath):
                        skipped += 1
                        continue
                    cls = classify_file(fname)
                    CDRFile.objects.create(
                        filename=fname,
                        file_path=fpath,
                        file_size=os.path.getsize(fpath),
                        file_hash=get_file_hash(fpath),
                        decoder_type=cls.decoder_type,
                        operator_code=cls.operator or operator,
                        vendor=cls.vendor or path_vendor,
                        network_element=cls.network_element or path_ne,
                        status=CDRFile.Status.PENDING,
                    )
                    collected += 1
                    self.stdout.write(f'registered {operator}/{path_vendor}/{path_ne}/{fname}')

        self.stdout.write(self.style.SUCCESS(
            f'collect_local done: {collected} registered, {skipped} duplicates skipped.'))
