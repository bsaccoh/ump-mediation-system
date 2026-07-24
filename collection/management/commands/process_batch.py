"""Decode many CDR files in parallel across CPU cores.

CDR decoding is CPU-bound and embarrassingly parallel, so throughput scales
with cores. Each worker classifies a file, registers a CDRFile, and runs the
stream processor (decode-only by default -> writes the output CSV, no record
DB inserts).

    # all operators' input trees, one worker per core
    python manage.py process_batch

    # a specific directory, 8 workers
    python manage.py process_batch --dir data/orange/input/huawei/msc --workers 8

    # limit to one operator's tree
    python manage.py process_batch --operator orange
"""
import multiprocessing as mp
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

# Map decoder type -> (module path, processor class name). Module-level so the
# worker function stays importable/picklable under the spawn start method.
PROCESSORS = {
    'MSC':  ('streams.msc.processor', 'MSCProcessor'),
    'IMS':  ('streams.ims.processor', 'IMSProcessor'),
    'PGW':  ('streams.pgw.processor', 'PGWProcessor'),
    'SGSN': ('streams.sgsn.processor', 'SGSNProcessor'),
    'SGW':  ('streams.sgw.processor', 'SGWProcessor'),
    'CBS':  ('streams.cbs.processor', 'CBSProcessor'),
}

ALLOWED_EXT = ('.dat', '.bin', '.asn', '.ber', '.unl', '.add', '.csv', '.txt')


def _worker_init():
    """Initialise Django + fresh DB connections in each spawned worker."""
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    from django.db import connections
    connections.close_all()


def _hash_one(path: str):
    """Return (path, content_hash). Runs in a pool worker (parallel pre-pass)."""
    from collection.services.deduplication import get_file_hash
    try:
        return (path, get_file_hash(path))
    except Exception:
        return (path, None)


def _process_one(job) -> dict:
    """Classify + register + process a single file. Runs in a pool worker.

    `job` is (path, file_hash, archive)."""
    import importlib
    from collection.models import CDRFile
    from collection.services.file_detector import classify_file
    from collection.services.storage import archive_file

    path, file_hash, archive = job
    fname = os.path.basename(path)
    started = time.time()
    try:
        cls = classify_file(fname)
        decoder = cls.decoder_type
        entry = PROCESSORS.get(decoder)
        if entry is None:
            return {'file': fname, 'status': 'SKIPPED', 'reason': f'no processor for {decoder}'}

        # Register the file with status=PROCESSING so the post_save signal does
        # NOT also queue it (it only fires for PENDING) — we process inline here.
        cdr = CDRFile.objects.create(
            filename=fname, file_path=path,
            file_size=os.path.getsize(path), file_hash=file_hash,
            decoder_type=decoder, operator_code=cls.operator or '',
            vendor=cls.vendor or '', network_element=cls.network_element or '',
            status=CDRFile.Status.PROCESSING,
        )

        mod, cls_name = entry
        processor = getattr(importlib.import_module(mod), cls_name)()
        ok, message = processor.process(cdr.pk)
        cdr.refresh_from_db()

        archived = None
        if ok and archive:
            try:
                archived = archive_file(path, cls.operator, cls.vendor,
                                        cls.network_element, decoder)
                CDRFile.objects.filter(pk=cdr.pk).update(file_path=archived)
            except Exception as exc:  # don't fail a good decode over a move error
                archived = f'(archive failed: {str(exc).splitlines()[0][:80]})'

        return {
            'file': fname, 'status': 'DONE' if ok else 'FAILED',
            'records': cdr.records_valid, 'seconds': round(time.time() - started, 1),
            'message': message[:120], 'archived': archived,
        }
    except Exception as exc:
        return {'file': fname, 'status': 'ERROR', 'seconds': round(time.time() - started, 1),
                'message': str(exc)[:200]}


def _collect_files(directory: str | None, operator: str | None) -> list[str]:
    files: list[str] = []
    if directory:
        roots = [directory]
    else:
        data_dir = str(settings.DATA_DIR)
        ops = [operator] if operator else list(settings.OPERATORS)
        roots = [os.path.join(data_dir, op, 'input') for op in ops]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                if n.startswith('.') or not n.lower().endswith(ALLOWED_EXT):
                    continue
                files.append(os.path.join(dirpath, n))
    return sorted(files)


class Command(BaseCommand):
    help = 'Decode CDR files in parallel across CPU cores (decode-only by default).'

    def add_arguments(self, parser):
        parser.add_argument('--dir', help='Directory to scan (recursively).')
        parser.add_argument('--operator', help='Limit to one operator input tree.')
        parser.add_argument('--workers', type=int, default=os.cpu_count() or 4,
                            help='Parallel workers (default: CPU count).')
        parser.add_argument('--reprocess', action='store_true',
                            help='Re-decode files even if already processed (by hash).')
        parser.add_argument('--no-archive', action='store_true',
                            help='Do NOT move processed inputs into archive/.')

    def handle(self, *args, **opts):
        files = _collect_files(opts.get('dir'), opts.get('operator'))
        if not files:
            self.stdout.write(self.style.WARNING('No files found to process.'))
            return

        workers = max(1, min(opts['workers'], len(files)))
        persist = getattr(settings, 'CDR_PERSIST_RECORDS', False)
        reprocess = opts['reprocess']
        archive = not opts['no_archive']
        self.stdout.write(
            f'{len(files)} file(s), {workers} worker(s) '
            f'(persist_to_db={persist}, archive={archive}, reprocess={reprocess}). '
            f'Hashing for duplicate detection...')

        t0 = time.time()
        ctx = mp.get_context('spawn')
        with ctx.Pool(processes=workers, initializer=_worker_init) as pool:
            # --- Phase 1: hash everything in parallel -----------------------
            hashes = dict(pool.map(_hash_one, files))

            # --- Phase 2 (main): decide unique vs duplicate -----------------
            unique_jobs, duplicates = self._dedup(files, hashes, reprocess)
            dup_count = self._handle_duplicates(duplicates, archive)

            self.stdout.write(
                f'{len(unique_jobs)} to decode, {dup_count} duplicate(s) '
                f'set aside.')

            # --- Phase 3: decode the unique files in parallel ---------------
            jobs = [(p, hashes.get(p), archive) for p in unique_jobs]
            done = failed = total_records = 0
            n = 0
            for r in pool.imap_unordered(_process_one, jobs):
                n += 1
                if r['status'] == 'DONE':
                    done += 1
                    total_records += r.get('records', 0)
                    self.stdout.write(
                        f"  [{n}/{len(jobs)}] {r['file']}: "
                        f"{r.get('records',0):,} rec in {r.get('seconds')}s")
                else:
                    failed += 1
                    self.stdout.write(self.style.WARNING(
                        f"  [{n}/{len(jobs)}] {r['file']}: "
                        f"{r['status']} — {r.get('message') or r.get('reason','')}"))

        elapsed = time.time() - t0
        rate = total_records / elapsed if elapsed else 0
        self.stdout.write(self.style.SUCCESS(
            f'Done: {done} decoded, {dup_count} duplicate, {failed} failed, '
            f'{total_records:,} records in {elapsed:.1f}s '
            f'({rate:,.0f} rec/s across {workers} workers).'))

    def _dedup(self, files, hashes, reprocess):
        """Split files into (unique_to_process, duplicates). A file is a
        duplicate if its content hash was already processed in a prior run
        (unless --reprocess) or appears earlier in this same batch."""
        from collection.models import CDRFile

        batch_hashes = {h for h in hashes.values() if h}
        already = set()
        if not reprocess and batch_hashes:
            already = set(
                CDRFile.objects.filter(
                    file_hash__in=batch_hashes, status=CDRFile.Status.COMPLETED
                ).values_list('file_hash', flat=True)
            )

        unique, duplicates, seen = [], [], set()
        for path in files:
            h = hashes.get(path)
            if h and h in already:
                duplicates.append((path, 'already processed (prior run)'))
            elif h and h in seen:
                duplicates.append((path, 'duplicate within this batch'))
            else:
                if h:
                    seen.add(h)
                unique.append(path)
        return unique, duplicates

    def _handle_duplicates(self, duplicates, archive):
        """Record duplicates as CDRFile(status=DUPLICATE) and move them out of
        the input tree into the per-operator duplicates/ dir."""
        from collection.models import CDRFile
        from collection.services.file_detector import classify_file
        from collection.services.deduplication import get_file_hash
        from collection.services.storage import duplicate_file

        for path, reason in duplicates:
            fname = os.path.basename(path)
            cls = classify_file(fname)
            try:
                CDRFile.objects.create(
                    filename=fname, file_path=path, file_size=os.path.getsize(path),
                    file_hash=get_file_hash(path), decoder_type=cls.decoder_type,
                    operator_code=cls.operator or '', vendor=cls.vendor or '',
                    network_element=cls.network_element or '',
                    status=CDRFile.Status.DUPLICATE, error_message=reason,
                )
                if archive:
                    duplicate_file(path, cls.operator, cls.vendor,
                                   cls.network_element, cls.decoder_type)
                self.stdout.write(f'  DUPLICATE: {fname} ({reason})')
            except Exception as exc:
                self.stdout.write(self.style.WARNING(
                    f'  duplicate handling failed for {fname}: {exc}'))
        return len(duplicates)
