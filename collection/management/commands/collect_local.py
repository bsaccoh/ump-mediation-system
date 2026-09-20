"""Scan the per-operator input tree and register new CDR files.

Walks ``DATA_DIR/{operator}/input/{vendor}/{network_element}/`` and creates a
CDRFile (status PENDING -> signal triggers processing) for every file not yet
collected (deduplicated by content hash). Operator/vendor/network-element are
taken primarily from the filename classification (SourcePattern) and fall back
to the directory segments the file was found under.

File lifecycle: input → processing → archive.
The collector moves each file from the input (published) directory to the
processing directory before registering the CDRFile.  This way the signal
handler processes from the processing directory, and the input directory
only ever contains files waiting to be collected.

    python manage.py collect_local
    python manage.py collect_local --operator orange
"""
import os
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand

from collection.models import CDRFile
from collection.services.deduplication import get_file_hash, check_duplicate
from collection.services.file_detector import classify_file
from collection.services.paths import PathBuilder
from collection.services.storage import processing_storage_dir


class Command(BaseCommand):
    help = 'Scan DATA_DIR/{operator}/input/{vendor}/{ne}/ and register new files.'

    def add_arguments(self, parser):
        parser.add_argument('--operator', help='Limit to one operator code.')

    def handle(self, *args, **opts):
        only = opts.get('operator')
        collected = skipped = 0
        input_root = PathBuilder.input_published('unknown', 'unknown').parents[1]
        operators = sorted(path.name for path in input_root.iterdir() if path.is_dir()) if input_root.is_dir() else []
        for operator in operators:
            if only and operator != only:
                continue
            operator_root = input_root / operator
            if not operator_root.is_dir():
                continue
            for root, dirs, files in os.walk(operator_root):
                dirs[:] = [directory for directory in dirs if directory != 'staging']
                rel = os.path.relpath(root, operator_root).split(os.sep)
                stream = rel[0] if rel and rel[0] != '.' else ''
                cbs_substream = rel[1] if stream.lower() == 'cbs' and len(rel) > 1 else ''
                for fname in files:
                    if fname.startswith('.'):
                        continue
                    fpath = os.path.join(root, fname)
                    if check_duplicate(fpath):
                        skipped += 1
                        continue
                    cls = classify_file(fname)
                    file_hash = get_file_hash(fpath)
                    file_size = os.path.getsize(fpath)

                    # Move file from input to processing directory
                    proc_dir = processing_storage_dir(
                        cls.operator or operator,
                        cls.network_element or stream,
                        cls.decoder_type,
                        cbs_substream or None,
                    )
                    processing_path = os.path.join(proc_dir, fname)
                    if os.path.abspath(fpath) != os.path.abspath(processing_path):
                        shutil.move(fpath, processing_path)

                    CDRFile.objects.create(
                        filename=fname,
                        file_path=processing_path,
                        file_size=file_size,
                        file_hash=file_hash,
                        decoder_type=cls.decoder_type,
                        operator_code=cls.operator or operator,
                        vendor=cls.vendor or '',
                        network_element=cls.network_element or stream,
                        cbs_substream=cbs_substream,
                        status=CDRFile.Status.PENDING,
                    )
                    collected += 1
                    self.stdout.write(f'collected {operator}/{stream}/{fname} -> processing/')

        self.stdout.write(self.style.SUCCESS(
            f'collect_local done: {collected} collected, {skipped} duplicates skipped.'))
