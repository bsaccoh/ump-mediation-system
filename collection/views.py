"""Collection views - file upload, listing, and management."""
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
import json
import csv
from datetime import timedelta
from core.decorators import operator_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import DataSource, CDRFile, DistributionLog, ReplayLog
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

    files = files.order_by('-created_at')[:500]
    sources = DataSource.objects.filter(enabled=True)

    return render(request, 'collection/file_list.html', {
        'files': files,
        'sources': sources,
        'current_status': status,
        'current_source': source_id,
    })


@operator_required
def upload_file(request):
    """Handle CDR file upload."""
    if request.method != 'POST':
        sources = DataSource.objects.filter(enabled=True)
        from core.enums import DecoderType

        # Real canonical UMP filename detection rules (from file_detector / reference)
        filename_rules = [
            {'pattern': 'bFTMSX*.dat', 'detected_type': 'MSC'},
            {'pattern': '*pgw*.dat', 'detected_type': 'PGW'},
            {'pattern': '*sgsn*.dat', 'detected_type': 'SGSN'},
            {'pattern': '*sgw*.dat', 'detected_type': 'SGW'},
            {'pattern': '*ims*.dat', 'detected_type': 'IMS'},
            {'pattern': '*ocs*.dat', 'detected_type': 'OCS'},
            {'pattern': '*cbs*.dat', 'detected_type': 'CBS'},
        ]

        decoder_choices = [
            (DecoderType.MSC, 'MSC (Huawei ASN.1/BER)'),
            (DecoderType.PGW, 'PGW (3GPP PS Domain)'),
            (DecoderType.SGSN, 'SGSN (3GPP PS Domain)'),
            (DecoderType.SGW, 'SGW (3GPP PS Domain)'),
            (DecoderType.IMS, 'IMS (Huawei ATS9900 VoLTE/VoBB)'),
            (DecoderType.OCS, 'OCS Input'),
            (DecoderType.CBS, 'CBS Output'),
            (DecoderType.CSV, 'Pre-decoded CSV'),
        ]

        context = {
            'sources': sources,
            'decoder_choices': decoder_choices,
            'filename_rules': filename_rules,
        }
        return render(request, 'collection/upload.html', context)

    uploaded_files = request.FILES.getlist('files') or request.FILES.getlist('cdr_files')
    if not uploaded_files:
        messages.error(request, 'No files selected.')
        return redirect('collection:upload')

    source_id = request.POST.get('source_id') or None
    base_decoder_type = request.POST.get('decoder_type', 'AUTO')

    # Auto-resolve DataSource from decoder type when none selected
    def _resolve_source(decoder):
        if source_id:
            return source_id
        ds = DataSource.objects.filter(
            decoder_type=decoder, enabled=True,
        ).first()
        return ds.pk if ds else None

    success_count = 0
    duplicate_count = 0
    skipped_extensions = 0
    
    # Allowed extensions for safety during folder uploads
    ALLOWED_EXTENSIONS = ('.dat', '.bin', '.cdr', '.csv', '.txt', '.asn', '.asn1', '.ber', '.unl', '.add', '.xml', '.gz', '.zip')

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

        # Reject zero-byte files immediately
        if file_size == 0:
            os.remove(file_path)
            messages.warning(request, f'{uploaded.name}: rejected — file is empty (0 bytes).')
            continue

        file_hash = get_file_hash(file_path)

        # Check for duplicate
        if check_duplicate(file_path):
            os.remove(file_path)
            duplicate_count += 1
            continue

        # Create CDRFile record (signal will trigger processing)
        CDRFile.objects.create(
            source_id=_resolve_source(decoder_type),
            filename=uploaded.name,
            file_path=file_path,
            file_size=file_size,
            file_hash=file_hash,
            decoder_type=decoder_type,
            operator_code=cls.operator or '',
            vendor=cls.vendor or '',
            network_element=cls.network_element or '',
            uploaded_by=request.user,
            status=CDRFile.Status.COLLECTED,
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
        logger.debug("Could not load CDR records for file detail", exc_info=True)

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


def _format_file_size(bytes_val):
    """Format bytes into a human-readable string (B, KB, MB, GB)."""
    if not bytes_val or bytes_val <= 0:
        return "—"
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.1f} GB"


def _do_retry_single_log(log_id):
    """Re-run a single rule's delivery for the original CDR file."""
    from core.dispatcher import dispatch_cdr_file
    from portals.models import DistributionRule

    try:
        log = DistributionLog.objects.select_related('rule', 'output_portal', 'cdr_file').get(pk=log_id)
    except DistributionLog.DoesNotExist:
        return False, 'Delivery record not found.'

    if not log.rule or not log.cdr_file:
        return False, 'Cannot retry: original rule or file is missing.'

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
        return True, f'Retry succeeded — {summary.get("records", 0)} records delivered.'
    else:
        err = (summary or {}).get('error', 'unknown error')
        return False, f'Retry failed — {err}'


def _do_retry_bulk_logs(log_ids):
    """Retry multiple FAILED deliveries selected from the dashboard."""
    from core.dispatcher import dispatch_cdr_file
    from portals.models import DistributionRule
    from collections import defaultdict

    logs = list(
        DistributionLog.objects
        .filter(pk__in=log_ids, status=DistributionLog.Status.FAILED)
        .select_related('rule', 'cdr_file')
    )
    actionable = [l for l in logs if l.rule_id and l.cdr_file_id]
    skipped = len(logs) - len(actionable)

    if not actionable:
        return False, 'None of the selected rows are retryable (missing rule or file).'

    success = failed = 0
    errors = []
    by_file = defaultdict(list)
    rule_name_by_id = {}
    for l in actionable:
        by_file[l.cdr_file_id].append(l.rule_id)
        rule_name_by_id[l.rule_id] = l.rule.name

    for cdr_file_id, rule_ids in by_file.items():
        target_rule_ids = set(rule_ids)
        other_active = list(
            DistributionRule.objects.filter(is_active=True).exclude(pk__in=target_rule_ids)
        )
        DistributionRule.objects.filter(pk__in=[r.pk for r in other_active]).update(is_active=False)
        try:
            DistributionRule.objects.filter(pk__in=target_rule_ids, is_active=False).update(is_active=True)
            result = dispatch_cdr_file(cdr_file_id)
        finally:
            DistributionRule.objects.filter(pk__in=[r.pk for r in other_active]).update(is_active=True)

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
    if failed and success == 0:
        msg += ' First errors: ' + '; '.join(errors[:3])
        return False, msg
    elif failed:
        msg += ' First errors: ' + '; '.join(errors[:3])
        return True, msg
    return True, msg


@login_required
def distribution_log_retry(request, log_id):
    """Re-run a single rule's delivery for the original CDR file (HTML redirect flow)."""
    success, msg = _do_retry_single_log(log_id)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect(request.META.get('HTTP_REFERER') or 'collection:distribution_dashboard')


@login_required
def distribution_log_bulk_retry(request):
    """Retry multiple FAILED deliveries selected from the dashboard (HTML form flow)."""
    if request.method != 'POST':
        return redirect('collection:distribution_dashboard')

    log_ids = request.POST.getlist('log_ids')
    if not log_ids:
        messages.warning(request, 'No deliveries selected.')
        return redirect('collection:distribution_dashboard')

    success, msg = _do_retry_bulk_logs(log_ids)
    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect(request.META.get('HTTP_REFERER') or 'collection:distribution_dashboard')


@login_required
def distribution_log_retry_api(request):
    """AJAX endpoint to retry a single failed distribution."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    log_id = None
    if request.body:
        try:
            payload = json.loads(request.body.decode('utf-8'))
            log_id = payload.get('id')
        except Exception:
            logger.debug("Could not parse JSON body for retry request", exc_info=True)
    if not log_id:
        log_id = request.POST.get('id')

    if not log_id:
        return JsonResponse({'success': False, 'message': 'Missing delivery ID'}, status=400)

    success, msg = _do_retry_single_log(log_id)
    return JsonResponse({'success': success, 'message': msg}, status=200 if success else 400)


@login_required
def distribution_log_bulk_retry_api(request):
    """AJAX endpoint to retry selected failed distributions."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    ids = None
    if request.body:
        try:
            payload = json.loads(request.body.decode('utf-8'))
            ids = payload.get('ids')
        except Exception:
            logger.debug("Could not parse JSON body for bulk retry", exc_info=True)
    if not ids:
        ids = request.POST.getlist('ids') or request.POST.getlist('log_ids')

    if not ids:
        return JsonResponse({'success': False, 'message': 'No deliveries selected'}, status=400)

    success, msg = _do_retry_bulk_logs(ids)
    return JsonResponse({'success': success, 'message': msg}, status=200 if success else 400)


def _get_distribution_filtered_data(request):
    """Filter distribution logs and compute period stats."""
    status = request.GET.get('status', '').strip()
    portal = request.GET.get('portal', '').strip()
    stream = request.GET.get('stream', '').strip()
    period = request.GET.get('period', '7d').strip()
    filename = request.GET.get('filename', '').strip()

    now = timezone.now()
    start_dt = None
    if period == 'today':
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == '24h':
        start_dt = now - timedelta(hours=24)
    elif period == '7d':
        start_dt = now - timedelta(days=7)
    elif period == '30d':
        start_dt = now - timedelta(days=30)
    # If period == 'all' or empty, start_dt stays None

    base_qs = DistributionLog.objects.select_related('cdr_file', 'rule', 'output_portal')
    period_qs = base_qs
    if start_dt:
        period_qs = period_qs.filter(delivered_at__gte=start_dt)

    total_deliveries = period_qs.count()
    success_deliveries = period_qs.filter(status=DistributionLog.Status.SUCCESS).count()
    failed_deliveries = period_qs.filter(status=DistributionLog.Status.FAILED).count()
    skipped_deliveries = period_qs.filter(status=DistributionLog.Status.SKIPPED).count()

    def calc_rate(count, tot):
        if tot <= 0:
            return 0
        val = (count / tot) * 100
        return int(round(val)) if val == int(val) else round(val, 1)

    stats = {
        'total': total_deliveries,
        'success': success_deliveries,
        'failed': failed_deliveries,
        'skipped': skipped_deliveries,
        'success_rate': calc_rate(success_deliveries, total_deliveries),
        'failure_rate': calc_rate(failed_deliveries, total_deliveries),
        'skipped_rate': calc_rate(skipped_deliveries, total_deliveries),
    }

    filtered_qs = period_qs
    if status:
        filtered_qs = filtered_qs.filter(status=status)
    if portal:
        filtered_qs = filtered_qs.filter(output_portal_id=portal)
    if stream:
        filtered_qs = filtered_qs.filter(Q(rule__stream_type=stream) | Q(cdr_file__decoder_type=stream))
    if filename:
        filtered_qs = filtered_qs.filter(Q(filename__icontains=filename) | Q(cdr_file__filename__icontains=filename))

    filtered_qs = filtered_qs.order_by('-delivered_at')

    filters = {
        'status': status,
        'portal': portal,
        'stream': stream,
        'period': period,
        'filename': filename,
    }
    return filtered_qs, stats, filters


@login_required
def distribution_export(request):
    """Export deliveries matching current filters or selected IDs to CSV."""
    ids_str = request.GET.get('ids', '').strip()
    if ids_str:
        id_list = [int(i.strip()) for i in ids_str.split(',') if i.strip().isdigit()]
        qs = DistributionLog.objects.filter(pk__in=id_list).select_related('rule', 'output_portal', 'cdr_file').order_by('-delivered_at')
    else:
        qs, _, _ = _get_distribution_filtered_data(request)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="distribution_deliveries_{timestamp}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Delivered',
        'Rule',
        'Stream',
        'Portal',
        'Filename',
        'Records',
        'Size',
        'Retries',
        'Status',
    ])

    for log in qs:
        rule_name = log.rule.name if log.rule else (log.output_portal.name if log.output_portal else "—")
        stream_name = log.rule.stream_type if (log.rule and log.rule.stream_type) else (log.cdr_file.decoder_type if (log.cdr_file and log.cdr_file.decoder_type) else "—")
        portal_name = log.output_portal.name if log.output_portal else "—"
        retry_num = log.retry_count or (log.cdr_file.retry_count if log.cdr_file else 0)
        writer.writerow([
            log.delivered_at.strftime('%Y-%m-%d %H:%M:%S') if log.delivered_at else '—',
            rule_name,
            stream_name,
            portal_name,
            log.filename or (log.cdr_file.filename if log.cdr_file else '—'),
            log.record_count if log.record_count is not None else 0,
            _format_file_size(log.file_size),
            retry_num if retry_num else '—',
            log.status,
        ])

    return response


@login_required
def distribution_dashboard(request):
    """Distribution Dashboard matching master reference design."""
    from portals.models import OutputPortal

    filtered_qs, stats, filters = _get_distribution_filtered_data(request)

    try:
        per_page = int(request.GET.get('per_page', 10))
        if per_page not in (10, 25, 50, 100):
            per_page = 10
    except (ValueError, TypeError):
        per_page = 10

    paginator = Paginator(filtered_qs, per_page)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    deliveries = []
    for log in page_obj.object_list:
        rule_name = log.rule.name if log.rule else (log.output_portal.name if log.output_portal else "—")
        stream_name = log.rule.stream_type if (log.rule and log.rule.stream_type) else (log.cdr_file.decoder_type if (log.cdr_file and log.cdr_file.decoder_type) else "—")
        portal_name = log.output_portal.name if log.output_portal else "—"
        deliveries.append({
            'id': log.id,
            'delivered_at': log.delivered_at,
            'rule_name': rule_name,
            'stream': stream_name,
            'portal_name': portal_name,
            'filename': log.filename or (log.cdr_file.filename if log.cdr_file else "—"),
            'record_count': log.record_count if log.record_count is not None else 0,
            'formatted_size': _format_file_size(log.file_size),
            'retry_count': log.retry_count or (log.cdr_file.retry_count if log.cdr_file else 0),
            'status': log.status,
            'detail_url': reverse('collection:distribution_log_view', args=[log.id]),
            'download_url': reverse('collection:distribution_log_download', args=[log.id]),
        })

    output_portals = OutputPortal.objects.all().order_by('name')
    streams = [
        {'code': 'MSC', 'name': 'MSC'},
        {'code': 'PGW', 'name': 'PGW'},
        {'code': 'SGSN', 'name': 'SGSN'},
        {'code': 'SGW', 'name': 'SGW'},
        {'code': 'IMS', 'name': 'IMS'},
        {'code': 'OCS', 'name': 'OCS'},
        {'code': 'CBS', 'name': 'CBS'},
    ]

    total_records = paginator.count
    page_start = page_obj.start_index() if total_records > 0 else 0
    page_end = page_obj.end_index() if total_records > 0 else 0

    query_params = request.GET.copy()
    query_params.pop('page', None)
    preserved_query_string = query_params.urlencode()

    context = {
        'stats': stats,
        'deliveries': deliveries,
        'portals': output_portals,
        'streams': streams,
        'filters': filters,
        'total_records': total_records,
        'page_start': page_start,
        'page_end': page_end,
        'page_obj': page_obj,
        'per_page': per_page,
        'query_string': preserved_query_string,
        'distribution_url': reverse('collection:distribution_dashboard'),
        'files_url': reverse('collection:file_list'),
        'retry_distribution_url': reverse('collection:distribution_log_retry_api'),
        'retry_selected_url': reverse('collection:distribution_log_bulk_retry_api'),
        'export_distribution_url': reverse('collection:distribution_export'),
    }

    return render(request, 'collection/distribution_dashboard.html', context)


@operator_required
def reprocess_file(request, pk):
    """Re-process a file: delete existing records and run the processor again.

    Deletes any previously-decoded rows for this file so the run is fully
    idempotent (no duplicates), then triggers the per-stream processor
    synchronously in a background thread so the request returns fast.
    """
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

    # 3. Dispatch via Celery or queue worker (avoids ad-hoc threads)
    from collection.signals import dispatch_processing
    dispatch_processing(decoder, cdr_file.pk, cdr_file.filename)

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
        logger.debug("Could not clean distribution logs for file", exc_info=True)
    return count


@operator_required
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


# ---------------------------------------------------------------------------
# Selective downstream replay
# ---------------------------------------------------------------------------

@login_required
def output_portals_api(request):
    """GET: return list of active OutputPortals for the replay portal dropdown."""
    from portals.models import OutputPortal
    portals = (
        OutputPortal.objects
        .filter(is_active=True)
        .values('id', 'name', 'portal_type', 'output_format')
        .order_by('name')
    )
    return JsonResponse({'portals': list(portals)})


@login_required
def replay_upload(request):
    """POST: accept uploaded CDR files + portal_id and queue selective replay tasks.

    For each uploaded file the view:
    1. Looks up an existing COMPLETED CDRFile by filename (most recent match).
    2. Saves the uploaded bytes to a per-session temp directory.
    3. Queues replay_uploaded_file Celery task.

    Returns JSON with per-file results so the UI can show inline status.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    portal_id = request.POST.get('portal_id')
    if not portal_id:
        return JsonResponse({'error': 'portal_id is required'}, status=400)

    try:
        portal_id = int(portal_id)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'portal_id must be an integer'}, status=400)

    from portals.models import OutputPortal
    portal = OutputPortal.objects.filter(pk=portal_id, is_active=True).first()
    if not portal:
        return JsonResponse({'error': 'Portal not found or inactive'}, status=404)

    uploaded_files = request.FILES.getlist('files')
    if not uploaded_files:
        return JsonResponse({'error': 'No files uploaded'}, status=400)

    import tempfile, uuid
    temp_dir = os.path.join(tempfile.gettempdir(), 'ump_replay')
    os.makedirs(temp_dir, exist_ok=True)

    from collection.tasks import replay_uploaded_file as replay_task

    results = []
    for f in uploaded_files:
        filename = os.path.basename(f.name)

        existing = (
            CDRFile.objects
            .filter(filename=filename)
            .order_by('-created_at')
            .first()
        )

        if not existing:
            results.append({
                'filename': filename,
                'status': 'REJECTED',
                'reason': 'No processing history — use normal Upload & Process',
            })
            continue

        if existing.status == CDRFile.Status.FAILED:
            results.append({
                'filename': filename,
                'status': 'REJECTED',
                'reason': 'File previously failed — use Reprocess, not Replay',
            })
            continue

        if existing.status != CDRFile.Status.COMPLETED:
            results.append({
                'filename': filename,
                'status': 'REJECTED',
                'reason': f'File is currently {existing.status} — wait for it to complete',
            })
            continue

        suffix = os.path.splitext(filename)[1] or '.dat'
        temp_path = os.path.join(temp_dir, f'{uuid.uuid4().hex}{suffix}')
        try:
            with open(temp_path, 'wb') as tmp:
                for chunk in f.chunks():
                    tmp.write(chunk)
        except OSError as e:
            results.append({
                'filename': filename,
                'status': 'ERROR',
                'reason': f'Could not save temp file: {e}',
            })
            continue

        task = replay_task.delay(
            cdr_file_id=existing.pk,
            temp_path=temp_path,
            portal_id=portal_id,
            requested_by=str(request.user),
        )

        results.append({
            'filename': filename,
            'status': 'QUEUED',
            'task_id': task.id,
            'original_file_id': existing.pk,
            'portal': portal.name,
        })

    return JsonResponse({'results': results})
