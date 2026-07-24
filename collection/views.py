"""Collection views - file upload, listing, and management."""
import os
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404

from .models import DataSource, CDRFile, DistributionLog
from .services.file_detector import detect_decoder_type, classify_file
from .services.storage import input_storage_dir
from .services.deduplication import get_file_hash, check_duplicate


@login_required
def file_list(request):
    """List CDR files with filtering."""
    files = CDRFile.objects.select_related('source', 'uploaded_by')

    # Filters
    status = request.GET.get('status')
    if status:
        files = files.filter(status=status)

    source_id = request.GET.get('source')
    if source_id:
        files = files.filter(source_id=source_id)

    files = files[:100]
    sources = DataSource.objects.filter(enabled=True)

    return render(request, 'collection/file_list.html', {
        'files': files,
        'sources': sources,
        'current_status': status,
        'current_source': source_id,
    })


@login_required
def upload_file(request):
    """Handle CDR file upload."""
    if request.method != 'POST':
        sources = DataSource.objects.filter(enabled=True)
        return render(request, 'collection/upload.html', {'sources': sources})

    uploaded_files = request.FILES.getlist('files')
    if not uploaded_files:
        messages.error(request, 'No files selected.')
        return redirect('collection:upload')

    source_id = request.POST.get('source_id')
    base_decoder_type = request.POST.get('decoder_type', 'AUTO')
    
    success_count = 0
    duplicate_count = 0
    skipped_extensions = 0
    
    # Allowed extensions for safety during folder uploads
    ALLOWED_EXTENSIONS = ('.dat', '.bin', '.cdr', '.csv', '.txt', '.asn', '.ber', '.unl', '.add')

    for uploaded in uploaded_files:
        # Skip unsupported files during folder upload
        if not uploaded.name.lower().endswith(ALLOWED_EXTENSIONS):
            skipped_extensions += 1
            continue

        # Classify into operator / vendor / network element / decoder.
        cls = classify_file(uploaded.name)
        decoder_type = base_decoder_type
        if decoder_type == 'AUTO':
            decoder_type = cls.decoder_type

        # Store under the per-operator input tree:
        #   DATA_DIR/{operator}/input/{vendor}/{ne}/<original filename>
        # Vendor/operator/NE are directory segments only — keep the original name.
        upload_dir = input_storage_dir(
            cls.operator, cls.vendor, cls.network_element, decoder_type,
        )
        filename = uploaded.name
        file_path = os.path.join(upload_dir, filename)

        with open(file_path, 'wb+') as dest:
            for chunk in uploaded.chunks():
                dest.write(chunk)

        file_size = os.path.getsize(file_path)
        file_hash = get_file_hash(file_path)

        # Check for duplicate
        if check_duplicate(file_path):
            os.remove(file_path)
            duplicate_count += 1
            continue

        # Create CDRFile record (signal will trigger processing)
        CDRFile.objects.create(
            source_id=source_id if source_id else None,
            filename=uploaded.name,
            file_path=file_path,
            file_size=file_size,
            file_hash=file_hash,
            decoder_type=decoder_type,
            operator_code=cls.operator or '',
            vendor=cls.vendor or '',
            network_element=cls.network_element or '',
            uploaded_by=request.user,
            status=CDRFile.Status.PENDING,
        )
        success_count += 1

    # Aggregate feedback
    if success_count > 0:
        msg = f'Successfully uploaded {success_count} file(s) and queued for processing.'
        if duplicate_count > 0:
            msg += f' Skipped {duplicate_count} duplicate(s).'
        if skipped_extensions > 0:
            msg += f' Ignored {skipped_extensions} unsupported file(s).'
        messages.success(request, msg)
    elif duplicate_count > 0:
        messages.warning(request, f'All {duplicate_count} uploaded file(s) were skipped as duplicates.')
    else:
        messages.error(request, 'No valid files were uploaded.')

    return redirect('collection:file_list')


@login_required
def file_detail(request, pk):
    """View CDR file details, including a preview of the decoded records."""
    cdr_file = get_object_or_404(CDRFile, pk=pk)
    distribution_logs = (DistributionLog.objects
                         .filter(cdr_file=cdr_file)
                         .select_related('rule', 'output_portal')
                         .order_by('-delivered_at'))

    # Fetch a preview of decoded records (first 50) using the right model per stream
    records = []
    record_total = 0
    paired_count = 0          # only meaningful for IMS / MSC
    pair_capable = False      # the stream supports correlation
    pair_key_field = None     # ICID / call_reference — populated rate matters
    pair_key_populated = 0
    decoder = (cdr_file.decoder_type or '').upper()
    try:
        if decoder == 'MSC':
            from streams.msc.models import MSCRecord
            qs = MSCRecord.objects.filter(file=cdr_file).order_by('-start_time', '-id')
            record_total = qs.count()
            records = list(qs[:50])
            pair_capable = True
            pair_key_field = 'call_reference'
            paired_count = qs.filter(paired_record__isnull=False).count()
            pair_key_populated = qs.exclude(call_reference='').exclude(call_reference__isnull=True).count()
        elif decoder == 'IMS':
            from streams.ims.models import IMSRecord
            qs = IMSRecord.objects.filter(file=cdr_file).order_by('-start_time', '-id')
            record_total = qs.count()
            records = list(qs[:50])
            pair_capable = True
            pair_key_field = 'ICID'
            paired_count = qs.filter(paired_record__isnull=False).count()
            pair_key_populated = qs.exclude(icid='').exclude(icid__isnull=True).count()
        elif decoder == 'PGW':
            from streams.pgw.models import PGWRecord
            qs = PGWRecord.objects.filter(file=cdr_file).order_by('-id')
            record_total = qs.count()
            records = list(qs[:50])
        elif decoder == 'SGSN':
            from streams.sgsn.models import SGSNRecord
            qs = SGSNRecord.objects.filter(file=cdr_file).order_by('-id')
            record_total = qs.count()
            records = list(qs[:50])
        elif decoder == 'SGW':
            from streams.sgw.models import SGWRecord
            qs = SGWRecord.objects.filter(file=cdr_file).order_by('-id')
            record_total = qs.count()
            records = list(qs[:50])
        elif decoder == 'CBS':
            from streams.cbs.models import CBSRecord
            qs = CBSRecord.objects.filter(file=cdr_file).order_by('-id')
            record_total = qs.count()
            records = list(qs[:50])
    except Exception:
        pass

    # Pair-completeness stats (used by the File Detail badge)
    pair_pct = 0
    if pair_capable and pair_key_populated:
        pair_pct = round(100.0 * paired_count / pair_key_populated, 1)

    # Per-record processing errors (cap to last 10 for the inline panel)
    from collection.models import ProcessingError
    proc_errors = (ProcessingError.objects.filter(cdr_file=cdr_file)
                   .order_by('-created_at')[:10])
    proc_error_total = ProcessingError.objects.filter(cdr_file=cdr_file).count()

    return render(request, 'collection/file_detail.html', {
        'file': cdr_file,
        'distribution_logs': distribution_logs,
        'records': records,
        'record_total': record_total,
        'record_decoder': decoder,
        'pair_capable': pair_capable,
        'pair_key_field': pair_key_field,
        'pair_key_populated': pair_key_populated,
        'paired_count': paired_count,
        'orphan_count': pair_key_populated - paired_count,
        'pair_pct': pair_pct,
        'proc_errors': proc_errors,
        'proc_error_total': proc_error_total,
    })


def _resolve_log_path(log: DistributionLog) -> str:
    portal = log.output_portal
    if not portal or portal.portal_type != 'LOCAL':
        raise Http404('File only viewable for LOCAL portals')
    if not log.filename:
        raise Http404('Distribution log has no filename')
    
    directory = portal.resolve_directory(dt=log.delivered_at)
    path = os.path.join(directory, log.filename)
    if not os.path.isfile(path):
        raise Http404(f'File not found on disk: {path}')
    return path


@login_required
def distribution_log_view(request, log_id):
    """Show header + first/last rows of a delivered LOCAL file."""
    log = get_object_or_404(DistributionLog.objects.select_related('output_portal', 'rule', 'cdr_file'), pk=log_id)
    path = _resolve_log_path(log)
    max_rows = int(request.GET.get('rows', 200))
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        lines = []
        for i, line in enumerate(f):
            if i >= max_rows + 1:
                break
            lines.append(line.rstrip('\n'))
    header = lines[0] if lines else ''
    rows = lines[1:] if len(lines) > 1 else []
    delimiter = ','
    if log.output_portal and (log.output_portal.output_format or 'CSV').upper() == 'CSV':
        header_cells = header.split(delimiter)
        row_cells = [r.split(delimiter) for r in rows]
    else:
        header_cells, row_cells = None, None
    return render(request, 'collection/distribution_log_view.html', {
        'log': log,
        'path': path,
        'header': header,
        'header_cells': header_cells,
        'row_cells': row_cells,
        'rows_raw': rows,
        'shown': len(rows),
        'total_records': log.record_count,
    })


@login_required
def distribution_log_download(request, log_id):
    log = get_object_or_404(DistributionLog.objects.select_related('output_portal'), pk=log_id)
    path = _resolve_log_path(log)
    with open(path, 'rb') as f:
        data = f.read()
    fmt = (log.output_portal.output_format or 'CSV').upper()
    ctype = {'CSV': 'text/csv', 'JSON': 'application/json', 'XML': 'application/xml'}.get(fmt, 'application/octet-stream')
    response = HttpResponse(data, content_type=ctype)
    response['Content-Disposition'] = f'attachment; filename="{log.filename}"'
    return response


@login_required
def distribution_log_retry(request, log_id):
    """Re-run a single rule's delivery for the original CDR file.

    Replays the rule end-to-end so a new SUCCESS log row is added (the original
    FAILED row is preserved as audit history).
    """
    log = get_object_or_404(DistributionLog.objects.select_related('rule', 'output_portal', 'cdr_file'), pk=log_id)
    if not log.rule or not log.cdr_file:
        messages.error(request, 'Cannot retry: original rule or file is missing.')
        return redirect(request.META.get('HTTP_REFERER', 'collection:file_list'))
    from core.dispatcher import dispatch_cdr_file
    # Filter dispatcher to this single rule by temporarily disabling other active rules
    from portals.models import DistributionRule
    other_active = list(DistributionRule.objects.filter(is_active=True).exclude(pk=log.rule.pk))
    DistributionRule.objects.filter(pk__in=[r.pk for r in other_active]).update(is_active=False)
    try:
        if not log.rule.is_active:
            DistributionRule.objects.filter(pk=log.rule.pk).update(is_active=True)
        result = dispatch_cdr_file(log.cdr_file_id)
    finally:
        DistributionRule.objects.filter(pk__in=[r.pk for r in other_active]).update(is_active=True)
    summary = next((r for r in result if r.get('rule') == log.rule.name), None)
    if summary and summary.get('status') == 'SUCCESS':
        messages.success(request, f'Retry succeeded — {summary.get("records",0)} records delivered.')
    else:
        err = (summary or {}).get('error', 'unknown error')
        messages.error(request, f'Retry failed — {err}')
    return redirect(request.META.get('HTTP_REFERER') or 'collection:file_detail', pk=log.cdr_file_id)


@login_required
def distribution_log_bulk_retry(request):
    """Retry multiple FAILED deliveries selected from the dashboard."""
    if request.method != 'POST':
        return redirect('collection:distribution_dashboard')

    log_ids = request.POST.getlist('log_ids')
    if not log_ids:
        messages.warning(request, 'No deliveries selected.')
        return redirect('collection:distribution_dashboard')

    from core.dispatcher import dispatch_cdr_file
    from portals.models import DistributionRule

    # Only retry FAILED logs that still have a rule + cdr_file
    logs = list(
        DistributionLog.objects
        .filter(pk__in=log_ids, status=DistributionLog.Status.FAILED)
        .select_related('rule', 'cdr_file')
    )
    actionable = [l for l in logs if l.rule_id and l.cdr_file_id]
    skipped = len(logs) - len(actionable)

    if not actionable:
        messages.error(request, 'None of the selected rows are retryable (missing rule or file).')
        return redirect('collection:distribution_dashboard')

    success = failed = 0
    errors = []
    # Group by cdr_file so we minimise dispatcher calls per file
    from collections import defaultdict
    by_file = defaultdict(list)  # {cdr_file_id: [rule_id, ...]}
    rule_name_by_id = {}
    for l in actionable:
        by_file[l.cdr_file_id].append(l.rule_id)
        rule_name_by_id[l.rule_id] = l.rule.name

    for cdr_file_id, rule_ids in by_file.items():
        target_rule_ids = set(rule_ids)
        # Disable other active rules during dispatch so only the targets fire
        other_active = list(
            DistributionRule.objects.filter(is_active=True).exclude(pk__in=target_rule_ids)
        )
        DistributionRule.objects.filter(pk__in=[r.pk for r in other_active]).update(is_active=False)
        try:
            DistributionRule.objects.filter(pk__in=target_rule_ids, is_active=False).update(is_active=True)
            result = dispatch_cdr_file(cdr_file_id)
        finally:
            DistributionRule.objects.filter(pk__in=[r.pk for r in other_active]).update(is_active=True)

        # Tally per rule
        for rule_id in target_rule_ids:
            name = rule_name_by_id.get(rule_id, '?')
            summary = next((r for r in result if r.get('rule') == name), None)
            if summary and summary.get('status') == 'SUCCESS':
                success += 1
            else:
                failed += 1
                errors.append(f'{name}: {(summary or {}).get("error", "no result")}')

    parts = [f'{success} delivery/ies succeeded']
    if failed:
        parts.append(f'{failed} failed')
    if skipped:
        parts.append(f'{skipped} non-retryable skipped')
    msg = ', '.join(parts) + '.'
    if failed:
        messages.warning(request, msg + ' First errors: ' + '; '.join(errors[:3]))
    else:
        messages.success(request, msg)
    return redirect(request.META.get('HTTP_REFERER') or 'collection:distribution_dashboard')


@login_required
def distribution_dashboard(request):
    """Top-level distribution log listing with filters."""
    from portals.models import OutputPortal, DistributionRule
    qs = DistributionLog.objects.select_related('cdr_file', 'rule', 'output_portal').order_by('-delivered_at')
    status = request.GET.get('status', '')
    portal_id = request.GET.get('portal', '')
    stream = request.GET.get('stream', '')
    days = request.GET.get('days', '7')
    if status:
        qs = qs.filter(status=status)
    if portal_id:
        qs = qs.filter(output_portal_id=portal_id)
    if stream:
        qs = qs.filter(rule__stream_type=stream)
    if days and days != 'all':
        from django.utils import timezone
        from datetime import timedelta
        try:
            qs = qs.filter(delivered_at__gte=timezone.now() - timedelta(days=int(days)))
        except ValueError:
            pass
    totals = {
        'all': DistributionLog.objects.count(),
        'success': DistributionLog.objects.filter(status='SUCCESS').count(),
        'failed': DistributionLog.objects.filter(status='FAILED').count(),
        'skipped': DistributionLog.objects.filter(status='SKIPPED').count(),
    }
    logs = list(qs[:500])
    return render(request, 'collection/distribution_dashboard.html', {
        'logs': logs,
        'totals': totals,
        'portals': OutputPortal.objects.all().order_by('name'),
        'streams': ['MSC', 'PGW', 'SGSN', 'SGW'],
        'filters': {'status': status, 'portal': portal_id, 'stream': stream, 'days': days},
    })


@login_required
def reprocess_file(request, pk):
    """Re-process a file: delete existing records and run the processor again.

    Deletes any previously-decoded rows for this file so the run is fully
    idempotent (no duplicates), then triggers the per-stream processor
    synchronously in a background thread so the request returns fast.
    """
    import threading

    cdr_file = get_object_or_404(CDRFile, pk=pk)
    if cdr_file.status not in (CDRFile.Status.FAILED, CDRFile.Status.COMPLETED):
        messages.warning(
            request,
            f'File "{cdr_file.filename}" is in status {cdr_file.status}; '
            f'cannot reprocess.'
        )
        return redirect('collection:file_detail', pk=pk)

    # 1. Clear previously-decoded records so re-processing is idempotent
    decoder = (cdr_file.decoder_type or '').upper()
    cleared = _clear_records_for_file(cdr_file, decoder)

    # 2. Reset CDRFile state
    cdr_file.status = CDRFile.Status.PENDING
    cdr_file.retry_count += 1
    cdr_file.error_message = ''
    cdr_file.records_total = 0
    cdr_file.records_valid = 0
    cdr_file.records_invalid = 0
    cdr_file.records_duplicate = 0
    cdr_file.processing_started = None
    cdr_file.processing_completed = None
    cdr_file.save()

    # 3. Run the processor in a background thread so the UI doesn't block
    from collection.signals import _process_sync
    threading.Thread(
        target=_process_sync,
        args=(decoder, cdr_file.pk, cdr_file.filename),
        daemon=True,
    ).start()

    messages.success(
        request,
        f'File "{cdr_file.filename}" re-queued for processing '
        f'(cleared {cleared} existing record(s)).'
    )
    return redirect('collection:file_detail', pk=pk)


def _clear_records_for_file(cdr_file, decoder: str) -> int:
    """Delete previously-decoded records for this CDRFile.

    Returns the number of rows deleted.  Used by ``reprocess_file`` to keep
    re-runs idempotent and prevent duplicate-record accumulation.
    """
    decoder = (decoder or '').upper()
    try:
        if decoder == 'MSC':
            from streams.msc.models import MSCRecord
            count, _ = MSCRecord.objects.filter(file=cdr_file).delete()
        elif decoder == 'IMS':
            from streams.ims.models import IMSRecord
            count, _ = IMSRecord.objects.filter(file=cdr_file).delete()
        elif decoder == 'PGW':
            from streams.pgw.models import PGWRecord
            count, _ = PGWRecord.objects.filter(file=cdr_file).delete()
        elif decoder == 'SGSN':
            from streams.sgsn.models import SGSNRecord
            count, _ = SGSNRecord.objects.filter(file=cdr_file).delete()
        elif decoder == 'SGW':
            from streams.sgw.models import SGWRecord
            count, _ = SGWRecord.objects.filter(file=cdr_file).delete()
        elif decoder == 'CBS':
            from streams.cbs.models import CBSRecord
            count, _ = CBSRecord.objects.filter(file=cdr_file).delete()
        else:
            count = 0
    except Exception:
        count = 0
    # Also wipe stale distribution logs for this file
    try:
        from collection.models import DistributionLog
        DistributionLog.objects.filter(cdr_file=cdr_file).delete()
    except Exception:
        pass
    return count


@login_required
def poll_sftp_now(request, source_id):
    """Manually trigger SFTP poll for a data source."""
    source = get_object_or_404(DataSource, pk=source_id)
    if source.source_type != DataSource.SourceType.SFTP:
        messages.error(request, f'{source.name} is not an SFTP source.')
        return redirect('collection:file_list')

    try:
        from collection.services.sftp_collector import poll_source
        stats = poll_source(source)
        messages.success(
            request,
            f'SFTP poll complete for "{source.name}": '
            f'{stats["collected"]} new files, {stats["skipped"]} skipped.'
        )
        if stats['errors']:
            messages.warning(request, f'Errors: {"; ".join(stats["errors"][:3])}')
    except Exception as e:
        messages.error(request, f'SFTP poll failed: {e}')

    return redirect('collection:file_list')
