"""Dashboard views."""
import csv
import io
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.db.models import Count, Sum, Avg, Q, F, Min, Max
from django.utils import timezone

from collection.models import CDRFile, DataSource, DistributionPortal
from streams.msc.models import MSCRecord
from streams.ims.models import IMSRecord
from streams.pgw.models import PGWRecord
from streams.sgsn.models import SGSNRecord
from streams.sgw.models import SGWRecord

RAT_TYPE_NAMES = {
    '1': 'UTRAN', '2': 'GERAN', '3': 'WLAN', '4': 'GAN',
    '5': 'HSPA_Evolution', '6': 'EUTRAN', '7': 'Virtual',
    '8': 'EUTRAN_NB_IoT', '9': 'LTE_M', '10': 'NR',
}


@login_required
def index(request):
    """Main dashboard with overview statistics."""
    now = timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # File stats
    total_files = CDRFile.objects.count()
    files_today = CDRFile.objects.filter(created_at__gte=today).count()
    files_completed = CDRFile.objects.filter(status=CDRFile.Status.COMPLETED).count()
    files_failed = CDRFile.objects.filter(status=CDRFile.Status.FAILED).count()
    files_pending = CDRFile.objects.filter(
        status__in=[CDRFile.Status.PENDING, CDRFile.Status.PROCESSING]
    ).count()

    # Record stats — aggregate across all four streams
    msc_total = MSCRecord.objects.count()
    pgw_total = PGWRecord.objects.count()
    sgsn_total = SGSNRecord.objects.count()
    sgw_total = SGWRecord.objects.count()
    total_records = msc_total + pgw_total + sgsn_total + sgw_total

    msc_today = MSCRecord.objects.filter(created_at__gte=today).count()
    pgw_today = PGWRecord.objects.filter(created_at__gte=today).count()
    sgsn_today = SGSNRecord.objects.filter(created_at__gte=today).count()
    sgw_today = SGWRecord.objects.filter(created_at__gte=today).count()
    records_today = msc_today + pgw_today + sgsn_today + sgw_today

    # Per-stream counts for breakdown card
    stream_counts = [
        {'stream': 'MSC (Voice/SMS)', 'count': msc_total, 'today': msc_today},
        {'stream': 'PGW (4G Data)',   'count': pgw_total, 'today': pgw_today},
        {'stream': 'SGSN (2G/3G)',    'count': sgsn_total, 'today': sgsn_today},
        {'stream': 'SGW (4G S-GW)',   'count': sgw_total, 'today': sgw_today},
    ]

    # Service type breakdown (MSC only — PGW/SGSN always 'DATA')
    service_stats = (
        MSCRecord.objects
        .values('service_type')
        .annotate(count=Count('id'), total_duration=Sum('duration'))
        .order_by('-count')
    )

    # Record type breakdown (top 10) — combine MSC + PGW + SGSN record_type counts
    from collections import defaultdict

    rt_counts = defaultdict(int)
    for item in MSCRecord.objects.values('record_type').annotate(count=Count('id')):
        rt_counts[item['record_type']] += item['count']
    for item in PGWRecord.objects.values('record_type').annotate(count=Count('id')):
        rt_counts[item['record_type']] += item['count']
    for item in SGSNRecord.objects.values('record_type').annotate(count=Count('id')):
        rt_counts[item['record_type']] += item['count']
    for item in SGWRecord.objects.values('record_type').annotate(count=Count('id')):
        rt_counts[item['record_type']] += item['count']
    record_type_stats = sorted(
        [{'record_type': k, 'count': v} for k, v in rt_counts.items()],
        key=lambda x: -x['count']
    )[:10]

    # Recent files
    recent_files = CDRFile.objects.select_related('source')[:10]

    # Active sources
    active_sources = DataSource.objects.filter(enabled=True).count()

    return render(request, 'dashboard/index.html', {
        'total_files': total_files,
        'files_today': files_today,
        'files_completed': files_completed,
        'files_failed': files_failed,
        'files_pending': files_pending,
        'total_records': total_records,
        'records_today': records_today,
        'msc_total': msc_total,
        'pgw_total': pgw_total,
        'sgsn_total': sgsn_total,
        'sgw_total': sgw_total,
        'stream_counts': stream_counts,
        'service_stats': service_stats,
        'record_type_stats': record_type_stats,
        'recent_files': recent_files,
        'active_sources': active_sources,
    })


# =============================================================================
# Dashboard KPIs — PM-KPI sample (7 charts)
# =============================================================================

@login_required
def set_active_operator(request):
    """Switch the active operator (stored in session). All data-plane queries
    for subsequent requests read from that operator's database."""
    from django.conf import settings
    from core.middleware import SESSION_KEY
    code = (request.POST.get('operator') or request.GET.get('operator') or '').lower()
    if code in settings.OPERATORS:
        request.session[SESSION_KEY] = code
        messages.success(request, f'Active operator switched to {code}.')
    else:
        messages.error(request, f'Unknown operator: {code}')
    return redirect(request.META.get('HTTP_REFERER') or reverse('dashboard:index'))


@login_required
@require_POST
def run_collection(request):
    """UI trigger: scan input trees + decode new files now (background process)."""
    from collection.services.runner import launch_batch
    operator = (request.POST.get('operator') or '').strip().lower() or None
    try:
        info = launch_batch(operator)
        messages.success(
            request,
            f'Collection started for {info["operator"]} (pid {info["pid"]}). '
            f'New files are being decoded in the background — refresh shortly.')
    except Exception as exc:
        messages.error(request, f'Could not start collection: {exc}')
    return redirect(request.META.get('HTTP_REFERER') or reverse('dashboard:index'))


@login_required
def processing_summary_api(request):
    """Volume summary sourced from CDRFile (populated even in decode-only mode):
    files + records by operator / stream / day, service-type split, duplicates.
    Powers a dashboard that stays alive when records aren't persisted."""
    from collections import Counter
    from django.db.models.functions import TruncDate

    try:
        days = max(1, min(int(request.GET.get('days', 30)), 365))
    except (TypeError, ValueError):
        days = 30
    since = timezone.now() - timedelta(days=days)

    done = CDRFile.objects.filter(status=CDRFile.Status.COMPLETED, created_at__gte=since)

    totals = done.aggregate(files=Count('id'), records=Sum('records_valid'))
    by_operator = list(
        done.values('operator_code')
        .annotate(files=Count('id'), records=Sum('records_valid'))
        .order_by('-records')
    )
    by_stream = list(
        done.values('decoder_type')
        .annotate(files=Count('id'), records=Sum('records_valid'))
        .order_by('-records')
    )
    by_day = list(
        done.annotate(day=TruncDate('created_at')).values('day')
        .annotate(files=Count('id'), records=Sum('records_valid'))
        .order_by('day')
    )

    svc = Counter()
    for rbt in done.values_list('records_by_type', flat=True):
        if isinstance(rbt, dict):
            for k, v in rbt.items():
                try:
                    svc[k] += int(v)
                except (TypeError, ValueError):
                    pass

    status_counts = dict(
        CDRFile.objects.filter(created_at__gte=since)
        .values_list('status').annotate(n=Count('id'))
    )

    return JsonResponse({
        'window_days': days,
        'totals': {
            'files': totals['files'] or 0,
            'records': totals['records'] or 0,
            'duplicates': status_counts.get(CDRFile.Status.DUPLICATE, 0),
            'failed': status_counts.get(CDRFile.Status.FAILED, 0),
        },
        'by_operator': [
            {'operator': r['operator_code'] or '(unclassified)',
             'files': r['files'], 'records': r['records'] or 0}
            for r in by_operator
        ],
        'by_stream': [
            {'stream': r['decoder_type'] or '(none)',
             'files': r['files'], 'records': r['records'] or 0}
            for r in by_stream
        ],
        'by_service_type': [{'service_type': k, 'records': v} for k, v in svc.most_common()],
        'by_day': [
            {'day': r['day'].isoformat() if r['day'] else '',
             'files': r['files'], 'records': r['records'] or 0}
            for r in by_day
        ],
    })


@login_required
def dashboard_kpis_api(request):
    """JSON payload powering the 7 PM-KPI charts on the main dashboard.

    Returns one nested dict per KPI, ready for Chart.js consumption.  All
    aggregates are computed from current data across MSC / PGW / SGSN /
    SGW streams; an optional ``?days=N`` query narrows the window for the
    trend charts (default 180 days = 6 months).
    """
    from collections import defaultdict
    from core.utils.operators import classify_operator

    try:
        window_days = max(30, min(int(request.GET.get('days', 180)), 730))
    except (TypeError, ValueError):
        window_days = 180
    now = timezone.now()
    window_start = now - timedelta(days=window_days)

    # Optional explicit date range (YYYY-MM-DD). When both present, every KPI
    # below is restricted to records whose start_time falls inside the range.
    from datetime import datetime as _dt
    def _parse(s):
        try:
            return _dt.strptime(s, '%Y-%m-%d')
        except (TypeError, ValueError):
            return None
    range_start = _parse(request.GET.get('start'))
    range_end   = _parse(request.GET.get('end'))
    if range_end:
        range_end = range_end + timedelta(days=1)  # inclusive end
    has_range = bool(range_start and range_end)

    def _scope(qs):
        if has_range:
            return qs.filter(start_time__gte=range_start, start_time__lt=range_end)
        return qs

    # ---- 1. Call Records by Call Type (donut) -----------------------
    # Bucket MSC + PGW records into the 5 user-visible categories.
    call_buckets = {
        'Voice MO': 0, 'Voice MT': 0, 'SMS MO': 0, 'SMS MT': 0,
        'International': 0, 'Data Session': 0,
    }
    MOC_TYPES    = {'MOC', 'GWO', 'GWOUT'}
    MTC_TYPES    = {'MTC', 'GWI', 'GWIN', 'CF', 'RCF', 'CallForwarding'}
    SMSMO_TYPES  = {'SMSMO', 'SMS-MO', 'SMSMO_IW'}
    SMSMT_TYPES  = {'SMSMT', 'SMS-MT', 'SMSMT_GW'}

    msc_qs = _scope(MSCRecord.objects).values('record_type', 'call_category').annotate(n=Count('id'))
    for r in msc_qs:
        rt = (r['record_type'] or '').upper()
        cat = (r['call_category'] or '').upper()
        n = r['n']
        if 'INTERNATIONAL' in cat:
            call_buckets['International'] += n
        elif rt in SMSMO_TYPES:
            call_buckets['SMS MO'] += n
        elif rt in SMSMT_TYPES:
            call_buckets['SMS MT'] += n
        elif rt in MOC_TYPES:
            call_buckets['Voice MO'] += n
        elif rt in MTC_TYPES:
            call_buckets['Voice MT'] += n
    call_buckets['Data Session'] += (
        _scope(PGWRecord.objects).count()
        + _scope(SGSNRecord.objects).count()
        + _scope(SGWRecord.objects).count()
    )

    # ---- 2. Incoming vs Outgoing vs Transit (bar) -------------------
    incoming = _scope(MSCRecord.objects).filter(
        record_type__in=['MTC', 'GWI', 'GWIN', 'SMSMT', 'SMS-MT', 'SMSMT_GW'],
    ).count()
    outgoing = _scope(MSCRecord.objects).filter(
        record_type__in=['MOC', 'GWO', 'GWOUT', 'SMSMO', 'SMS-MO', 'SMSMO_IW'],
    ).count()
    transit  = _scope(MSCRecord.objects).filter(
        record_type__in=['CF', 'RCF', 'CallForwarding', 'TRANSIT', 'ROAMING_FORWARDING'],
    ).count()

    # ---- 3. Total Data Usage by Technology (bar) --------------------
    # Aggregate bytes from PGW (4G), SGSN (2G/3G), SGW (4G).
    # rat_type lookup is heterogeneous — accept both numeric codes and labels.
    def _classify_rat(rat: str) -> str:
        s = (rat or '').strip().upper()
        if s in ('1', 'UTRAN', '3G'):                  return '3G'
        if s in ('2', 'GERAN', '2G'):                  return '2G'
        if s in ('6', 'EUTRAN', '4G', 'EUTRAN_NB_IOT', '8', 'LTE_M', '9'): return '4G'
        if s in ('10', 'NR', '5G'):                    return '5G'
        return '4G'  # default bucket for unknown — Orange SL is majority 4G

    tech_bytes = {'2G': 0, '3G': 0, '4G': 0, '5G': 0}
    for Model in (PGWRecord, SGSNRecord, SGWRecord):
        for r in _scope(Model.objects).values('rat_type').annotate(
            up=Sum('data_volume_up'), dn=Sum('data_volume_down'),
        ):
            bucket = _classify_rat(r['rat_type'])
            tech_bytes[bucket] += int(r['up'] or 0) + int(r['dn'] or 0)
    # Convert bytes → GB for display
    tech_gb = {k: round(v / (1024**3), 3) for k, v in tech_bytes.items()}

    # ---- 4. Subscriber Growth Trend (line) --------------------------
    # Distinct IMSI per calendar month (last `window_days`).
    # SQL TruncMonth + DISTINCT COUNT works on PG; fall back to Python.
    from django.db.models.functions import TruncMonth
    sub_start = range_start if has_range else window_start
    sub_end   = range_end   if has_range else (now + timedelta(days=1))
    msc_by_month = (
        MSCRecord.objects
        .filter(start_time__gte=sub_start, start_time__lt=sub_end)
        .exclude(imsi='')
        .annotate(month=TruncMonth('start_time'))
        .values('month')
        .annotate(distinct_imsis=Count('imsi', distinct=True))
        .order_by('month')
    )
    months = [(r['month'].strftime('%Y-%m') if r['month'] else '', r['distinct_imsis'])
              for r in msc_by_month]

    # ---- 5. Inter-Operator Traffic (horizontal bar) -----------------
    # Top N (calling-operator, called-operator) pairs by record count.
    # Classify each end's MSISDN via SL operator-prefix map.
    inter_pairs = defaultdict(int)
    for r in (_scope(MSCRecord.objects)
              .filter(record_type__in=['MOC', 'MTC', 'GWO', 'GWI', 'GWOUT', 'GWIN'])
              .values('calling_number', 'called_number')
              .annotate(n=Count('id'))):
        a = classify_operator(r['calling_number'])
        b = classify_operator(r['called_number'])
        # Only keep operator-to-operator pairs (both ends are SL operators)
        if (a in ('Orange', 'Africell', 'Qcell', 'Smart', 'Sierratel') and
            b in ('Orange', 'Africell', 'Qcell', 'Smart', 'Sierratel') and a != b):
            inter_pairs[(a, b)] += r['n']
    inter_pairs_list = [
        {'pair': f'{a} → {b}', 'count': c}
        for (a, b), c in sorted(inter_pairs.items(), key=lambda kv: -kv[1])[:8]
    ]

    # ---- 6. Call Drop Rate by Operator (bar) ------------------------
    # Per-operator % of dropped calls (result_code='stableCallAbnormalTermination'
    # OR numeric 41/42/47) over total MOC+MTC.
    DROP_RESULT_CODES = {'stableCallAbnormalTermination', '41', '42', '47'}
    op_totals = defaultdict(int)
    op_drops  = defaultdict(int)
    for r in (_scope(MSCRecord.objects)
              .filter(record_type__in=['MOC', 'MTC'])
              .values('calling_number', 'result_code')
              .annotate(n=Count('id'))):
        op = classify_operator(r['calling_number'])
        if op not in ('Orange', 'Africell', 'Qcell', 'Smart', 'Sierratel'):
            continue
        op_totals[op] += r['n']
        if (r['result_code'] or '') in DROP_RESULT_CODES:
            op_drops[op] += r['n']
    drop_rates = [
        {'operator': op, 'drop_pct': round(100 * op_drops[op] / op_totals[op], 3)
                                     if op_totals[op] else 0.0}
        for op in ('Orange', 'Africell', 'Qcell', 'Smart', 'Sierratel')
        if op_totals[op] > 0
    ]

    # ---- 7. International Traffic Trend (line, in/out) --------------
    intl_monthly = defaultdict(lambda: {'in': 0, 'out': 0})
    intl_start = range_start if has_range else window_start
    intl_end   = range_end   if has_range else (now + timedelta(days=1))
    for r in (MSCRecord.objects
              .filter(start_time__gte=intl_start, start_time__lt=intl_end,
                      call_category__icontains='INTERNATIONAL')
              .annotate(month=TruncMonth('start_time'))
              .values('month', 'record_type')
              .annotate(n=Count('id'))):
        key = r['month'].strftime('%Y-%m') if r['month'] else ''
        rt = (r['record_type'] or '').upper()
        if rt in {'MTC', 'GWI', 'GWIN', 'SMSMT', 'SMS-MT'}:
            intl_monthly[key]['in'] += r['n']
        elif rt in {'MOC', 'GWO', 'GWOUT', 'SMSMO', 'SMS-MO'}:
            intl_monthly[key]['out'] += r['n']
    intl_months_sorted = sorted(k for k in intl_monthly if k)
    intl_in  = [intl_monthly[m]['in']  for m in intl_months_sorted]
    intl_out = [intl_monthly[m]['out'] for m in intl_months_sorted]

    return JsonResponse({
        'window_days': window_days,
        'call_records_by_type': {
            'labels': list(call_buckets.keys()),
            'data':   list(call_buckets.values()),
        },
        'direction_traffic': {
            'labels': ['Incoming', 'Outgoing', 'Transit'],
            'data':   [incoming, outgoing, transit],
        },
        'data_usage_by_tech': {
            'labels':    list(tech_gb.keys()),
            'data_gb':   list(tech_gb.values()),
            'data_bytes': list(tech_bytes.values()),
        },
        'subscriber_growth': {
            'labels': [m for m, _ in months],
            'data':   [n for _, n in months],
        },
        'inter_operator': {
            'labels': [r['pair']  for r in inter_pairs_list],
            'data':   [r['count'] for r in inter_pairs_list],
        },
        'drop_rate_by_operator': {
            'labels': [r['operator'] for r in drop_rates],
            'data':   [r['drop_pct'] for r in drop_rates],
        },
        'international_trend': {
            'labels':  intl_months_sorted,
            'incoming': intl_in,
            'outgoing': intl_out,
        },
    })


# =============================================================================
# Processing Queue
# =============================================================================

@login_required
def processing_queue(request):
    """Processing queue page — auto-refreshes via AJAX."""
    return render(request, 'dashboard/processing_queue.html')


@login_required
def processing_queue_api(request):
    """JSON endpoint for processing queue data."""
    now = timezone.now()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    def serialize_files(qs):
        result = []
        for f in qs:
            duration = None
            if f.processing_started and f.processing_completed:
                duration = round((f.processing_completed - f.processing_started).total_seconds(), 1)
            elif f.processing_started:
                duration = round((now - f.processing_started).total_seconds(), 1)

            result.append({
                'id': f.pk,
                'filename': f.filename,
                'source': f.source.name if f.source else '-',
                'decoder_type': f.decoder_type,
                'status': f.status,
                'file_size': f.file_size,
                'records_total': f.records_total,
                'records_valid': f.records_valid,
                'records_invalid': f.records_invalid,
                'success_rate': f.success_rate,
                'error_message': f.error_message[:200] if f.error_message else '',
                'processing_duration': duration,
                'retry_count': f.retry_count,
                'created_at': f.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'uploaded_by': f.uploaded_by.username if f.uploaded_by else '-',
            })
        return result

    processing = CDRFile.objects.select_related('source', 'uploaded_by').filter(
        status=CDRFile.Status.PROCESSING
    ).order_by('-processing_started')

    pending = CDRFile.objects.select_related('source', 'uploaded_by').filter(
        status=CDRFile.Status.PENDING
    ).order_by('created_at')

    completed = CDRFile.objects.select_related('source', 'uploaded_by').filter(
        status=CDRFile.Status.COMPLETED,
        processing_completed__gte=last_24h
    ).order_by('-processing_completed')[:50]

    failed = CDRFile.objects.select_related('source', 'uploaded_by').filter(
        status=CDRFile.Status.FAILED,
        created_at__gte=last_7d
    ).order_by('-created_at')[:20]

    # Summary stats
    total_today = CDRFile.objects.filter(created_at__gte=now.replace(hour=0, minute=0, second=0)).count()
    completed_today = CDRFile.objects.filter(
        status=CDRFile.Status.COMPLETED,
        processing_completed__gte=now.replace(hour=0, minute=0, second=0)
    ).count()
    records_today = CDRFile.objects.filter(
        status=CDRFile.Status.COMPLETED,
        processing_completed__gte=now.replace(hour=0, minute=0, second=0)
    ).aggregate(total=Sum('records_valid'))['total'] or 0

    return JsonResponse({
        'processing': serialize_files(processing),
        'pending': serialize_files(pending),
        'completed': serialize_files(completed),
        'failed': serialize_files(failed),
        'summary': {
            'processing_count': processing.count(),
            'pending_count': pending.count(),
            'completed_count': completed.count(),
            'failed_count': failed.count(),
            'total_today': total_today,
            'completed_today': completed_today,
            'records_today': records_today,
        }
    })


@login_required
@require_POST
def stop_all_processing(request):
    """Mark all PROCESSING and PENDING files as FAILED immediately."""
    now = timezone.now()
    stopped = CDRFile.objects.filter(
        status__in=[CDRFile.Status.PROCESSING, CDRFile.Status.PENDING]
    )
    count = stopped.count()
    stopped.update(
        status=CDRFile.Status.FAILED,
        error_message=f'Manually stopped by {request.user.username} at {now.strftime("%Y-%m-%d %H:%M:%S")} UTC',
        processing_completed=now,
    )
    messages.warning(request, f'Stopped {count} job(s). Files marked as FAILED.')
    return redirect(reverse('dashboard:processing_queue'))


# =============================================================================
# Unified CDR Search
# =============================================================================

STREAM_MODELS = {
    'MSC':  MSCRecord,
    'IMS':  IMSRecord,
    'PGW':  PGWRecord,
    'SGSN': SGSNRecord,
    'SGW':  SGWRecord,
}


def _build_unified_queryset(params, stream):
    """Build filtered queryset for any stream."""
    model = STREAM_MODELS.get(stream)
    if not model:
        return None, 'Unknown stream'

    calling = params.get('calling_number', '').strip()
    called = params.get('called_number', '').strip()
    imsi = params.get('imsi', '').strip()
    imei = params.get('imei', '').strip()
    service_type = params.get('service_type', '').strip()
    record_type = params.get('record_type', '').strip()
    source_id = params.get('source_id', '').strip()
    start_date = params.get('start_date', '').strip()
    end_date = params.get('end_date', '').strip()
    apn = params.get('apn', '').strip()
    rat_type = params.get('rat_type', '').strip()

    query = model.objects.select_related('file', 'source')
    filters = []

    if calling:
        query = query.filter(calling_number__icontains=calling)
        filters.append(f'calling={calling}')
    if called:
        query = query.filter(called_number__icontains=called)
        filters.append(f'called={called}')
    # For IMS the imsi filter is applied below with a wider OR over
    # calling_imsi / called_imsi, so skip the strict match here.
    if imsi and stream != 'IMS':
        query = query.filter(imsi__icontains=imsi)
        filters.append(f'imsi={imsi}')
    if imei:
        query = query.filter(imei__icontains=imei)
        filters.append(f'imei={imei}')

    # MSC-specific filters
    if stream == 'MSC':
        paired = (params.get('paired') or '').strip().lower()
        file_id = (params.get('file_id') or '').strip()
        if paired == 'yes':
            query = query.filter(paired_record__isnull=False)
            filters.append('paired=yes')
        elif paired == 'no':
            query = query.filter(paired_record__isnull=True)
            filters.append('paired=no')
        if file_id.isdigit():
            query = query.filter(file_id=int(file_id))
        if service_type:
            query = query.filter(service_type=service_type)
            filters.append(f'service={service_type}')
        if record_type:
            variant_groups = {
                'SMS-MT':         ['SMS-MT', 'SMSMT', 'SIP_SMSMT', 'SMSMT_GW'],
                'SMSMT':          ['SMS-MT', 'SMSMT', 'SIP_SMSMT', 'SMSMT_GW'],
                'SMS-MO':         ['SMS-MO', 'SMSMO', 'SIP_SMSMO', 'SMSMO_IW'],
                'SMSMO':          ['SMS-MO', 'SMSMO', 'SIP_SMSMO', 'SMSMO_IW'],
                'CallForwarding': ['CallForwarding', 'CALL_FORWARDING', 'ROAMING_FORWARDING', 'CFW', 'MTRF', 'CF'],
                'CF':             ['CallForwarding', 'CALL_FORWARDING', 'ROAMING_FORWARDING', 'CFW', 'MTRF', 'CF'],
                # Gateway records are stored as the short form GWI / GWO
                # (from the BIG_DATA CSV's CALL_TYPE), so the dropdown's
                # GWIN / GWOUT values must also match the short form.
                'GWIN':           ['GWI', 'GWIN'],
                'GWOUT':          ['GWO', 'GWOUT'],
                'GWI':            ['GWI', 'GWIN'],
                'GWO':            ['GWO', 'GWOUT'],
            }
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            all_types = []
            for t in types:
                variants = variant_groups.get(t)
                if variants:
                    all_types.extend(variants)
                else:
                    all_types.append(t)
            query = query.filter(record_type__in=all_types)
            filters.append(f'type={",".join(types)}')

    # PGW-specific filters
    if stream == 'PGW':
        if apn:
            query = query.filter(apn__icontains=apn)
            filters.append(f'apn={apn}')
        if rat_type:
            query = query.filter(rat_type=rat_type)
            filters.append(f'rat={rat_type}')
        if record_type:
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            query = query.filter(record_type__in=types)
            filters.append(f'type={",".join(types)}')

    # SGSN-specific filters
    if stream == 'SGSN':
        if apn:
            query = query.filter(apn__icontains=apn)
            filters.append(f'apn={apn}')
        if rat_type:
            query = query.filter(rat_type__icontains=rat_type)
            filters.append(f'rat={rat_type}')
        if record_type:
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            query = query.filter(record_type__in=types)
            filters.append(f'type={",".join(types)}')

    # SGW-specific filters
    if stream == 'SGW':
        if apn:
            query = query.filter(apn__icontains=apn)
            filters.append(f'apn={apn}')
        if rat_type:
            query = query.filter(rat_type=rat_type)
            filters.append(f'rat={rat_type}')
        if record_type:
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            query = query.filter(record_type__in=types)
            filters.append(f'type={",".join(types)}')

    # IMS-specific filters
    if stream == 'IMS':
        sip_method   = params.get('sip_method', '').strip()
        role_of_node = params.get('role_of_node', '').strip()
        session_id   = params.get('session_id', '').strip()
        calling_imsi = params.get('calling_imsi', '').strip()
        called_imsi  = params.get('called_imsi', '').strip()
        paired       = (params.get('paired') or '').strip().lower()
        file_id      = (params.get('file_id') or '').strip()
        if paired == 'yes':
            query = query.filter(paired_record__isnull=False)
            filters.append('paired=yes')
        elif paired == 'no':
            query = query.filter(paired_record__isnull=True)
            filters.append('paired=no')
        if file_id.isdigit():
            query = query.filter(file_id=int(file_id))
        if service_type:
            query = query.filter(service_type=service_type)
            filters.append(f'service={service_type}')
        if sip_method:
            query = query.filter(sip_method__iexact=sip_method)
            filters.append(f'sip={sip_method}')
        if role_of_node:
            query = query.filter(role_of_node__iexact=role_of_node)
            filters.append(f'role={role_of_node}')
        if session_id:
            query = query.filter(session_id__icontains=session_id)
            filters.append(f'session={session_id}')
        # When the user types into the generic "IMSI" filter, also widen the
        # match to calling_imsi / called_imsi so terminating-records with no
        # served-IMSI still show up.
        if imsi:
            query = query.filter(
                Q(imsi__icontains=imsi) |
                Q(calling_imsi__icontains=imsi) |
                Q(called_imsi__icontains=imsi)
            )
        if calling_imsi:
            query = query.filter(calling_imsi__icontains=calling_imsi)
            filters.append(f'calling_imsi={calling_imsi}')
        if called_imsi:
            query = query.filter(called_imsi__icontains=called_imsi)
            filters.append(f'called_imsi={called_imsi}')
        if record_type:
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            query = query.filter(record_type__in=types)
            filters.append(f'type={",".join(types)}')

    if source_id:
        query = query.filter(source_id=source_id)
        filters.append(f'source={source_id}')

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(
                Q(start_time__gte=start_dt) | Q(created_at__gte=start_dt)
            )
            filters.append(f'from={start_date}')
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(
                Q(start_time__lt=end_dt) | Q(created_at__lt=end_dt)
            )
            filters.append(f'to={end_date}')
        except ValueError:
            pass

    desc = ', '.join(filters) if filters else 'all records'
    return query, desc


@login_required
def cdr_search(request):
    """Unified CDR search page — all streams."""
    msc_count  = MSCRecord.objects.count()
    ims_count  = IMSRecord.objects.count()
    pgw_count  = PGWRecord.objects.count()
    sgsn_count = SGSNRecord.objects.count()
    sgw_count  = SGWRecord.objects.count()
    sources    = DataSource.objects.filter(enabled=True).order_by('name')

    return render(request, 'dashboard/cdr_search.html', {
        'msc_count':  msc_count,
        'ims_count':  ims_count,
        'pgw_count':  pgw_count,
        'sgsn_count': sgsn_count,
        'sgw_count':  sgw_count,
        'total_records': msc_count + ims_count + pgw_count + sgsn_count + sgw_count,
        'sources': sources,
    })


@login_required
def cdr_search_api(request):
    """Unified CDR search API endpoint (POST)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    stream = request.POST.get('stream', 'MSC').upper()
    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 20))

    if stream not in STREAM_MODELS:
        return JsonResponse({'success': False, 'message': f'Unknown stream: {stream}'}, status=400)

    query, _ = _build_unified_queryset(request.POST, stream)
    total = query.count()

    if total == 0:
        model = STREAM_MODELS[stream]
        total_in_db = model.objects.count()
        if total_in_db > 0:
            message = f'No {stream} records match your filters. ({total_in_db:,} total in DB). Try Clear then Search.'
        else:
            message = f'No {stream} records found. Upload and process {stream} CDR files first.'

        return JsonResponse({
            'success': True, 'records': [], 'stream': stream,
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'stats': {},
            'message': message,
        })

    offset = (page - 1) * per_page
    records_qs = query.order_by('-created_at')[offset:offset + per_page]
    pages = (total + per_page - 1) // per_page

    records = []
    stats = {}

    if stream == 'MSC':
        for rec in records_qs:
            records.append({
                'id': rec.pk,
                'record_type': rec.record_type,
                'service_type': rec.service_type,
                'call_direction': rec.call_direction,
                'calling_number': rec.calling_number,
                'called_number': rec.called_number,
                'imsi': rec.imsi,
                'imei': rec.imei,
                'msc_id': rec.msc_id,
                'smsc_address': rec.smsc_address,
                'cell_id': rec.cell_id,
                'lac': rec.lac,
                'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                'duration': rec.duration,
                'originating_trunk': rec.originating_trunk,
                'terminating_trunk': rec.terminating_trunk,
                'result_code': rec.result_code,
                'rat_type': rec.rat_type,
                'call_category': rec.call_category,
                'roaming_indicator': rec.roaming_indicator,
                'status': rec.status,
            })
        agg = query.aggregate(
            total_duration=Sum('duration'),
            avg_duration=Avg('duration'),
        )
        stats = {
            'total_records': total,
            'total_duration': agg['total_duration'] or 0,
            'avg_duration': round(agg['avg_duration'] or 0, 2),
        }

    elif stream == 'PGW':
        try:
            for rec in records_qs:
                records.append({
                    'id': rec.pk,
                    'record_type': rec.record_type,
                    'calling_number': rec.calling_number,
                    'apn': rec.apn,
                    'imsi': rec.imsi,
                    'imei': rec.imei,
                    'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                    'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                    'duration': rec.duration,
                    'data_volume_up': rec.data_volume_up,
                    'data_volume_down': rec.data_volume_down,
                    'data_volume_mb': rec.data_volume_mb,
                    'rat_type': rec.rat_type,
                    'rat_type_name': RAT_TYPE_NAMES.get(rec.rat_type, rec.rat_type),
                    'pdn_type': rec.pdn_type,
                    'pgw_address': rec.pgw_address,
                    'node_id': rec.node_id,
                    'cause_for_closing': rec.cause_for_closing,
                    'serving_plmn': rec.serving_plmn,
                    'is_roaming': rec.is_roaming,
                    'cell_id': rec.cell_id,
                    'status': rec.status,
                })
        except Exception as e:
            import traceback
            print(f"ERROR in record loop: {e}")
            traceback.print_exc()
            return JsonResponse({'success': False, 'message': f'Error processing records: {str(e)}'}, status=500)
        
        # Aggregate data_volume_up and data_volume_down as integers in Python
        try:
            data_volumes = query.values_list('data_volume_up', 'data_volume_down')
            total_data_up = sum(int(x[0]) if x[0] and str(x[0]).strip() else 0 for x in data_volumes)
            total_data_down = sum(int(x[1]) if x[1] and str(x[1]).strip() else 0 for x in data_volumes)
        except Exception as e:
            import traceback
            print(f"ERROR in aggregation: {e}")
            traceback.print_exc()
            return JsonResponse({'success': False, 'message': f'Error aggregating data: {str(e)}'}, status=500)
        avg_duration = query.aggregate(avg_duration=Avg('duration'))['avg_duration'] or 0
        total_bytes = total_data_up + total_data_down
        stats = {
            'total_records': total,
            'total_data_up': total_data_up,
            'total_data_down': total_data_down,
            'total_data_mb': round(total_bytes / (1024 * 1024), 2) if total_bytes else 0,
            'avg_duration': round(avg_duration, 2),
        }

    elif stream == 'SGSN':
        for rec in records_qs:
            records.append({
                'id': rec.pk,
                'record_type': rec.record_type,
                'service_type': rec.service_type,
                'calling_number': rec.calling_number,
                'apn': rec.apn,
                'imsi': rec.imsi,
                'imei': rec.imei,
                'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                'duration': rec.duration,
                'data_volume_up': rec.data_volume_up,
                'data_volume_down': rec.data_volume_down,
                'data_volume_mb': rec.data_volume_mb,
                'rat_type': rec.rat_type,
                'pdp_type': rec.pdp_type,
                'sgsn_address': rec.sgsn_address,
                'ggsn_address': rec.ggsn_address,
                'node_id': rec.node_id,
                'cause_for_closing': rec.cause_for_closing,
                'serving_plmn': rec.serving_plmn,
                'cell_id': rec.cell_id,
                'lac': rec.lac,
                'rac': rec.rac,
                'is_roaming': rec.is_roaming,
                'status': rec.status,
            })
        data_volumes = query.values_list('data_volume_up', 'data_volume_down')
        total_data_up = sum(int(x[0]) if x[0] and str(x[0]).strip() else 0 for x in data_volumes)
        total_data_down = sum(int(x[1]) if x[1] and str(x[1]).strip() else 0 for x in data_volumes)
        avg_duration = query.aggregate(avg_duration=Avg('duration'))['avg_duration'] or 0
        total_bytes = total_data_up + total_data_down
        stats = {
            'total_records': total,
            'total_data_up': total_data_up,
            'total_data_down': total_data_down,
            'total_data_mb': round(total_bytes / (1024 * 1024), 2) if total_bytes else 0,
            'avg_duration': round(avg_duration, 2),
        }

    elif stream == 'SGW':
        for rec in records_qs:
            records.append({
                'id': rec.pk,
                'record_type': rec.record_type,
                'calling_number': rec.calling_number,
                'apn': rec.apn,
                'imsi': rec.imsi,
                'imei': rec.imei,
                'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                'duration': rec.duration,
                'data_volume_up': rec.data_volume_up,
                'data_volume_down': rec.data_volume_down,
                'data_volume_mb': rec.data_volume_mb,
                'rat_type': rec.rat_type,
                'rat_type_name': RAT_TYPE_NAMES.get(rec.rat_type, rec.rat_type),
                'pdn_type': rec.pdn_type,
                'sgw_address': rec.sgw_address,
                'pgw_address': rec.pgw_address,
                'node_id': rec.node_id,
                'cause_for_closing': rec.cause_for_closing,
                'serving_plmn': rec.serving_plmn,
                'is_roaming': rec.is_roaming,
                'cell_id': rec.cell_id,
                'lac': rec.lac,
                'status': rec.status,
            })
        data_volumes = query.values_list('data_volume_up', 'data_volume_down')
        total_data_up = sum(int(x[0]) if x[0] and str(x[0]).strip() else 0 for x in data_volumes)
        total_data_down = sum(int(x[1]) if x[1] and str(x[1]).strip() else 0 for x in data_volumes)
        avg_duration = query.aggregate(avg_duration=Avg('duration'))['avg_duration'] or 0
        total_bytes = total_data_up + total_data_down
        stats = {
            'total_records': total,
            'total_data_up': total_data_up,
            'total_data_down': total_data_down,
            'total_data_mb': round(total_bytes / (1024 * 1024), 2) if total_bytes else 0,
            'avg_duration': round(avg_duration, 2),
        }

    elif stream == 'IMS':
        for rec in records_qs:
            records.append({
                'id': rec.pk,
                'record_type':   rec.record_type,
                'service_type':  rec.service_type,
                'sip_method':    rec.sip_method,
                'role_of_node':  rec.role_of_node,
                'calling_number': rec.calling_number,
                'called_number':  rec.called_number,
                # A-party identifiers
                'calling_imsi':  rec.calling_imsi,
                'calling_min':   rec.calling_min,
                'calling_impi':  rec.calling_impi,
                # B-party identifiers
                'called_imsi':   rec.called_imsi,
                'called_min':    rec.called_min,
                'called_impi':   rec.called_impi,
                # Served
                'imsi':           rec.imsi,
                'msisdn':         rec.msisdn,
                'imei':           rec.imei,
                'private_user_identity': rec.private_user_identity,
                # Session
                'session_id':     rec.session_id,
                'icid':           rec.icid,
                # Network
                'node_address':   rec.node_address,
                'originating_ioi': rec.originating_ioi,
                'terminating_ioi': rec.terminating_ioi,
                'call_property':  rec.call_property,
                'call_category':  rec.call_category,
                'charging_category': rec.charging_category,
                'media_type':     rec.media_type,
                'roaming_indicator': rec.roaming_indicator,
                'start_time':  rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time':    rec.end_time.strftime('%Y-%m-%d %H:%M:%S')   if rec.end_time   else '',
                'duration':    rec.duration,
                'cause_for_closing': rec.cause_for_closing,
                'status': rec.status,
            })
        agg = query.aggregate(
            total_duration=Sum('duration'),
            avg_duration=Avg('duration'),
            voice_count=Count('id', filter=Q(service_type='VOICE')),
            sms_count=Count('id', filter=Q(service_type='SMS')),
            event_count=Count('id', filter=Q(service_type='EVENT')),
        )
        stats = {
            'total_records':  total,
            'total_duration': agg['total_duration'] or 0,
            'avg_duration':   round(agg['avg_duration'] or 0, 2),
            'voice_count':    agg['voice_count'] or 0,
            'sms_count':      agg['sms_count'] or 0,
            'event_count':    agg['event_count'] or 0,
        }

    return JsonResponse({
        'success': True,
        'records': records,
        'stream': stream,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
        'stats': stats,
    })


# =============================================================================
# Unified CDR Export
# =============================================================================

# Per-stream export-column mappings now live next to each stream:
#   streams/{msc,pgw,sgsn,sgw,ims}/headers.py
# This file imports them and re-exposes as the old constant names so
# nothing else in this module changes shape.
from streams.msc.headers  import MSC_HEADERS  as _MSC_HEADERS
from streams.pgw.headers  import PGW_HEADERS  as _PGW_HEADERS
from streams.sgsn.headers import SGSN_HEADERS as _SGSN_HEADERS
from streams.sgw.headers  import SGW_HEADERS  as _SGW_HEADERS

# Convert (header, source) → (source, header) for the existing export code,
# which expects (field_name, display_label) tuples.
MSC_EXPORT_COLUMNS  = [(s, h) for h, s in _MSC_HEADERS]

PGW_EXPORT_COLUMNS = [(s, h) for h, s in _PGW_HEADERS]

SGSN_EXPORT_COLUMNS = [(s, h) for h, s in _SGSN_HEADERS]
SGW_EXPORT_COLUMNS  = [(s, h) for h, s in _SGW_HEADERS]

IMS_EXPORT_COLUMNS = [
    ('record_type', 'Record Type'), ('service_type', 'Service Type'),
    ('sip_method', 'SIP Method'), ('role_of_node', 'Role of Node'),
    # Parties
    ('calling_number', 'Calling Number'), ('called_number', 'Called Number'),
    ('dialed_number', 'Dialed Number'), ('charged_party', 'Charged Party'),
    # A-party identifiers
    ('calling_imsi', 'Calling IMSI'), ('calling_min', 'Calling MIN'),
    ('calling_impi', 'Calling IMPI'),
    # B-party identifiers
    ('called_imsi', 'Called IMSI'), ('called_min', 'Called MIN'),
    ('called_impi', 'Called IMPI'),
    # Served subscriber
    ('imsi', 'IMSI'), ('msisdn', 'MSISDN'),
    ('imei', 'IMEI'), ('private_user_identity', 'Private User Identity (IMPI)'),
    # Session identifiers
    ('session_id', 'Session-ID (Call-ID)'), ('icid', 'ICID'),
    # Timing
    ('start_time', 'Start Time'), ('end_time', 'End Time'),
    ('duration', 'Duration (s)'), ('ringing_duration', 'Ringing Duration (s)'),
    # Network / routing
    ('node_address', 'Node Address'),
    ('originating_ioi', 'Originating IOI'), ('terminating_ioi', 'Terminating IOI'),
    ('msc_number', 'MSC Number'), ('vlr_number', 'VLR Number'),
    ('call_property', 'Call Property'),
    # Access network (derived from access_network_info)
    ('technology', 'Technology'),
    ('serving_plmn', 'Serving PLMN'),
    ('tac', 'TAC'), ('lac', 'LAC'),
    ('cell_id', 'Cell ID'), ('enodeb_id', 'eNodeB ID'),
    ('ue_ip', 'UE IP'),
    ('apn', 'APN'),
    # Call forwarding
    ('forwarded_number', 'Forwarded To'),
    ('redirecting_number', 'Redirecting Number'),
    ('diversion_reason', 'Diversion Reason'),
    ('diversion_count', 'Diversion Count'),
    # Classification / charging
    ('charging_category', 'Charging Category'),
    ('roaming_indicator', 'Roaming'), ('call_category', 'Call Category'),
    ('media_type', 'Media Type'),
    ('served_subscriber_type', 'Served Subscriber Type'),
    ('access_network_info', 'Access Network Info'),
    ('cause_for_closing', 'Cause for Closing'),
    ('service_reason_code', 'Service Reason Code'),
    ('online_charging_flag', 'Online Charging Flag'),
    ('supplementary_service', 'Supplementary Service'),
    ('service_context_id', 'Service Context ID'),
    ('sequence_number', 'Sequence #'),
    ('status', 'Status'),
]

MAX_EXPORT_ROWS = 100000


@login_required
def cdr_export(request):
    """Unified CDR export as CSV — all streams."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    stream = request.POST.get('stream', 'MSC').upper()
    if stream not in STREAM_MODELS:
        return JsonResponse({'error': f'Unknown stream: {stream}'}, status=400)

    query, filter_desc = _build_unified_queryset(request.POST, stream)
    total = query.count()

    if total > MAX_EXPORT_ROWS:
        return JsonResponse({
            'error': f'Too many records ({total:,}). Maximum export is {MAX_EXPORT_ROWS:,}. Add more filters.'
        }, status=400)
    if total == 0:
        return JsonResponse({'error': 'No records match the filters.'}, status=404)

    if stream == 'MSC':
        columns = MSC_EXPORT_COLUMNS
    elif stream == 'IMS':
        columns = IMS_EXPORT_COLUMNS
    elif stream == 'PGW':
        columns = PGW_EXPORT_COLUMNS
    elif stream == 'SGSN':
        columns = SGSN_EXPORT_COLUMNS
    else:
        columns = SGW_EXPORT_COLUMNS

    def stream_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([col[1] for col in columns])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        batch_size = 2000
        qs = query.order_by('-start_time', '-created_at')
        for offset in range(0, total, batch_size):
            rows = qs[offset:offset + batch_size]
            for rec in rows:
                row = []
                for field, _ in columns:
                    val = getattr(rec, field, '')
                    if hasattr(val, 'strftime'):
                        val = val.strftime('%Y-%m-%d %H:%M:%S')
                    row.append(val if val is not None else '')
                writer.writerow(row)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{stream.lower()}_export_{ts}.csv'
    response = StreamingHttpResponse(stream_csv(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# =============================================================================
# Unified CDR Detail
# =============================================================================

@login_required
def cdr_detail(request, stream, pk):
    """Unified CDR record detail view."""
    stream = stream.upper()
    if stream == 'MSC':
        record = get_object_or_404(MSCRecord, pk=pk)
        return render(request, 'dashboard/msc_detail.html', {'record': record, 'stream': 'MSC'})
    elif stream == 'IMS':
        record = get_object_or_404(IMSRecord, pk=pk)
        return render(request, 'dashboard/ims_detail.html', {'record': record, 'stream': 'IMS'})
    elif stream == 'PGW':
        record = get_object_or_404(PGWRecord, pk=pk)
        return render(request, 'dashboard/pgw_detail.html', {'record': record, 'stream': 'PGW'})
    elif stream == 'SGSN':
        record = get_object_or_404(SGSNRecord, pk=pk)
        return render(request, 'dashboard/sgsn_detail.html', {'record': record, 'stream': 'SGSN'})
    elif stream == 'SGW':
        record = get_object_or_404(SGWRecord, pk=pk)
        return render(request, 'dashboard/sgw_detail.html', {'record': record, 'stream': 'SGW'})
    else:
        return JsonResponse({'error': f'Unknown stream: {stream}'}, status=404)


# =============================================================================
# Subscriber View (remains MSC for now, can expand later)
# =============================================================================

@login_required
def subscriber_view(request):
    """Subscriber timeline page."""
    return render(request, 'dashboard/subscriber.html')


@login_required
def subscriber_api(request):
    """Subscriber timeline API — returns all activity (voice / SMS / IMS /
    data) for an MSISDN, deduplicated across paired CDR legs.

    Streams searched: MSC, IMS, PGW, SGSN, SGW.  When two records form a
    pair (ICID for IMS, call_reference for MSC), only the lower-id one is
    emitted so the timeline shows one row per real event.
    """
    from django.db.models import F
    from streams.ims.models import IMSRecord

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    msisdn = request.POST.get('msisdn', '').strip()
    if not msisdn or len(msisdn) < 5:
        return JsonResponse({'success': False, 'message': 'Enter a valid MSISDN (min 5 digits).'})

    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 30))

    # ----- Per-stream queries (pair-deduplicated) ----------------------------
    # Pair-dedup: keep only the record with the smaller id from each linked
    # pair so MOC+MTC (or IMS ORIG+TERM) appears as ONE timeline event.
    pair_dedup = Q(paired_record__isnull=True) | Q(id__lt=F('paired_record_id'))

    msc_query = (MSCRecord.objects.filter(
            Q(calling_number__icontains=msisdn) |
            Q(called_number__icontains=msisdn) |
            Q(charged_msisdn__icontains=msisdn)
        ).filter(pair_dedup)
         .select_related('file', 'source', 'paired_record'))

    ims_query = (IMSRecord.objects.filter(
            Q(calling_number__icontains=msisdn) |
            Q(called_number__icontains=msisdn) |
            Q(msisdn__icontains=msisdn) |
            Q(charged_party__icontains=msisdn)
        ).filter(pair_dedup)
         .select_related('file', 'source', 'paired_record'))

    pgw_query = PGWRecord.objects.filter(
        Q(calling_number__icontains=msisdn) | Q(imsi__icontains=msisdn)
    ).select_related('file', 'source')

    sgsn_query = SGSNRecord.objects.filter(
        Q(calling_number__icontains=msisdn) | Q(imsi__icontains=msisdn)
    ).select_related('file', 'source')

    sgw_query = SGWRecord.objects.filter(
        Q(calling_number__icontains=msisdn) | Q(imsi__icontains=msisdn)
    ).select_related('file', 'source')

    # ----- Totals & early-exit -----------------------------------------------
    msc_total  = msc_query.count()
    ims_total  = ims_query.count()
    pgw_total  = pgw_query.count()
    sgsn_total = sgsn_query.count()
    sgw_total  = sgw_query.count()
    total = msc_total + ims_total + pgw_total + sgsn_total + sgw_total

    if total == 0:
        return JsonResponse({
            'success': True, 'records': [], 'summary': {},
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'message': f'No activity found for "{msisdn}". Try a partial number.',
        })

    # ----- Summary aggregations ---------------------------------------------
    msc_agg = msc_query.aggregate(
        voice=Count('id', filter=Q(service_type='VOICE')),
        sms=Count('id',   filter=Q(service_type='SMS')),
        total_duration=Sum('duration'),
        avg_duration=Avg('duration', filter=Q(service_type='VOICE')),
    )
    ims_agg = ims_query.aggregate(
        voice=Count('id', filter=Q(service_type='VOICE')),
        sms=Count('id',   filter=Q(service_type='SMS')),
        event=Count('id', filter=Q(service_type='EVENT')),
        total_duration=Sum('duration'),
    )

    def _sum_bytes(qs):
        up = down = 0
        for u, d in qs.values_list('data_volume_up', 'data_volume_down'):
            try: up += int(u) if u and str(u).strip() else 0
            except Exception: pass
            try: down += int(d) if d and str(d).strip() else 0
            except Exception: pass
        return up, down

    pgw_up, pgw_down   = _sum_bytes(pgw_query)
    sgsn_up, sgsn_down = _sum_bytes(sgsn_query)
    sgw_up, sgw_down   = _sum_bytes(sgw_query)
    total_data_bytes   = pgw_up + pgw_down + sgsn_up + sgsn_down + sgw_up + sgw_down

    # Date range
    first_dates, last_dates = [], []
    for qs in (msc_query, ims_query, pgw_query, sgsn_query, sgw_query):
        d = qs.aggregate(first=Min('start_time'), last=Max('start_time'))
        if d.get('first'): first_dates.append(d['first'])
        if d.get('last'):  last_dates.append(d['last'])
    first_activity = min(first_dates) if first_dates else None
    last_activity  = max(last_dates) if last_dates else None

    # Primary IMSI/IMEI — prefer MSC, fall back to IMS
    imsi_qs = (msc_query.exclude(imsi='').values('imsi')
               .annotate(cnt=Count('id')).order_by('-cnt')[:1])
    if not imsi_qs:
        imsi_qs = (ims_query.exclude(imsi='').values('imsi')
                   .annotate(cnt=Count('id')).order_by('-cnt')[:1])
    imei_qs = (msc_query.exclude(imei='').values('imei')
               .annotate(cnt=Count('id')).order_by('-cnt')[:1])
    if not imei_qs:
        imei_qs = (ims_query.exclude(imei='').values('imei')
                   .annotate(cnt=Count('id')).order_by('-cnt')[:1])

    total_voice = (msc_agg.get('voice') or 0) + (ims_agg.get('voice') or 0)
    total_sms   = (msc_agg.get('sms')   or 0) + (ims_agg.get('sms')   or 0)
    total_data_sessions = pgw_total + sgsn_total + sgw_total
    total_duration = ((msc_agg.get('total_duration') or 0)
                      + (ims_agg.get('total_duration') or 0))
    avg_duration = round(msc_agg.get('avg_duration') or 0, 1)

    summary = {
        'msisdn': msisdn,
        'total_records': total,
        'total_voice': total_voice,
        'total_sms': total_sms,
        'total_other': ims_agg.get('event') or 0,   # IMS events (REGISTER / SUBSCRIBE …)
        'total_data_sessions': total_data_sessions,
        'total_duration': total_duration,
        'avg_duration': avg_duration,
        'total_data_mb': round(total_data_bytes / (1024 * 1024), 2) if total_data_bytes else 0,
        'first_activity': first_activity.strftime('%Y-%m-%d %H:%M') if first_activity else '-',
        'last_activity':  last_activity.strftime('%Y-%m-%d %H:%M') if last_activity else '-',
        'primary_imsi': imsi_qs[0]['imsi'] if imsi_qs else '-',
        'primary_imei': imei_qs[0]['imei'] if imei_qs else '-',
        # Per-stream breakdown for the new badges
        'count_msc':  msc_total,
        'count_ims':  ims_total,
        'count_pgw':  pgw_total,
        'count_sgsn': sgsn_total,
        'count_sgw':  sgw_total,
    }

    # ----- Event list (combined, pair-deduplicated) --------------------------
    combined = []

    # MSC events
    for rec in msc_query.order_by('-start_time', '-created_at')[:500]:
        role = 'CALLER'
        if msisdn in (rec.called_number or ''):
            role = 'CALLED'
        elif msisdn in (rec.charged_msisdn or '') and msisdn not in (rec.calling_number or ''):
            role = 'CHARGED'
        other_party = rec.called_number if role == 'CALLER' else rec.calling_number
        combined.append({
            'id': rec.pk,
            'stream': 'MSC',
            'record_type': rec.record_type,
            'service_type': rec.service_type,
            'role': role,
            'other_party': other_party or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.called_number,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.msc_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'technology': '',
            'paired_record_id': rec.paired_record_id,
            'status': rec.status,
        })

    # IMS events
    for rec in ims_query.order_by('-start_time', '-id')[:500]:
        role_node = (rec.role_of_node or '').upper()
        if msisdn in (rec.called_number or '') and 'TERM' in role_node:
            role = 'CALLED'
        elif msisdn in (rec.calling_number or '') and 'ORIG' in role_node:
            role = 'CALLER'
        elif msisdn in (rec.charged_party or '') and msisdn not in (rec.calling_number or ''):
            role = 'CHARGED'
        else:
            role = 'CALLER' if msisdn in (rec.calling_number or '') else 'CALLED'
        other_party = rec.called_number if role == 'CALLER' else rec.calling_number
        combined.append({
            'id': rec.pk,
            'stream': 'IMS',
            'record_type': rec.record_type,
            'service_type': rec.service_type,
            'role': role,
            'other_party': other_party or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.called_number,
            'imsi': rec.imsi or '',
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.msc_number or '',
            'cell_id': rec.cell_id or '',
            'lac': rec.lac or rec.tac or '',
            'technology': rec.technology or '',
            'paired_record_id': rec.paired_record_id,
            'status': rec.status,
            'sip_method': rec.sip_method or '',
            'call_type': rec.call_type or '',
        })

    # PGW events
    for rec in pgw_query.order_by('-start_time', '-created_at')[:500]:
        combined.append({
            'id': rec.pk,
            'stream': 'PGW',
            'record_type': rec.record_type,
            'service_type': 'DATA',
            'role': 'SUBSCRIBER',
            'other_party': rec.apn or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.apn,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.node_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'technology': RAT_TYPE_NAMES.get(rec.rat_type, ''),
            'status': rec.status,
            'data_volume_mb': rec.data_volume_mb,
        })

    # SGSN events (2G/3G data)
    for rec in sgsn_query.order_by('-start_time', '-created_at')[:200]:
        combined.append({
            'id': rec.pk,
            'stream': 'SGSN',
            'record_type': rec.record_type,
            'service_type': 'DATA',
            'role': 'SUBSCRIBER',
            'other_party': rec.apn or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.apn,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.node_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'technology': '3G' if str(rec.rat_type or '').upper() in ('UTRAN', '1') else '2G',
            'status': rec.status,
            'data_volume_mb': rec.data_volume_mb,
        })

    # SGW events (4G data)
    for rec in sgw_query.order_by('-start_time', '-created_at')[:200]:
        combined.append({
            'id': rec.pk,
            'stream': 'SGW',
            'record_type': rec.record_type,
            'service_type': 'DATA',
            'role': 'SUBSCRIBER',
            'other_party': rec.apn or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.apn,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.node_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'technology': RAT_TYPE_NAMES.get(rec.rat_type, '4G'),
            'status': rec.status,
            'data_volume_mb': rec.data_volume_mb,
        })

    # ----- Combine, sort, paginate ------------------------------------------
    combined.sort(key=lambda r: r.get('start_time_dt') or datetime.min, reverse=True)
    offset = (page - 1) * per_page
    page_records = combined[offset:offset + per_page]
    for r in page_records:
        r.pop('start_time_dt', None)
    pages = (total + per_page - 1) // per_page

    return JsonResponse({
        'success': True,
        'records': page_records,
        'summary': summary,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
    })


# =============================================================================
# File Registry
# =============================================================================

# Fallback display names for decoder types that have no DataSource configured
_DECODER_DISPLAY = {
    'MSC':  'MSC (Voice/SMS)',
    'PGW':  'PGW (4G Data)',
    'SGW':  'SGW (4G Serving GW)',
    'SGSN': 'SGSN (2G/3G Data)',
    'OCS':  'OCS Input',
    'CBS':  'CBS Output',
}


def _build_acq_tree():
    """Build acquisition tree from enabled DataSource records.

    Returns a list of decoder-type groups, each with a list of source leaves.
    """
    sources = DataSource.objects.filter(enabled=True).order_by('decoder_type', 'name')
    groups = {}
    for src in sources:
        dt = src.decoder_type
        if dt == 'AUTO':
            continue  # AUTO sources don't belong to a specific portal
        if dt not in groups:
            groups[dt] = {
                'decoder': dt,
                'label': _DECODER_DISPLAY.get(dt, dt),
                'sources': [],
                'total': 0,
                'completed': 0,
            }
        qs = CDRFile.objects.filter(source=src)
        total = qs.count()
        completed = qs.filter(status='COMPLETED').count()
        groups[dt]['sources'].append({
            'id': src.pk,
            'name': src.name,
            'total': total,
            'completed': completed,
        })
        groups[dt]['total'] += total
        groups[dt]['completed'] += completed

    # Also surface decoder types that have files but no DataSource
    for dt in CDRFile.objects.values_list('decoder_type', flat=True).distinct():
        if dt and dt not in groups and dt != 'AUTO':
            qs = CDRFile.objects.filter(decoder_type=dt)
            total = qs.count()
            groups[dt] = {
                'decoder': dt,
                'label': _DECODER_DISPLAY.get(dt, dt),
                'sources': [],
                'total': total,
                'completed': qs.filter(status='COMPLETED').count(),
            }

    return list(groups.values())


def _build_dist_tree():
    """Build distribution tree from enabled DistributionPortal records.

    Portals with a Vendor FK are grouped by vendor (preferred).
    Portals without a vendor fall back to the group/group_label fields.
    """
    portals = (DistributionPortal.objects
               .filter(enabled=True)
               .select_related('vendor')
               .order_by('vendor__name', 'group', 'label'))
    groups = {}
    for p in portals:
        if p.vendor and p.vendor.enabled:
            g_id = f'vendor_{p.vendor.pk}'
            g_label = p.vendor.name
        else:
            g_id = p.group or 'other'
            g_label = p.group_label or p.group or 'Other'

        if g_id not in groups:
            groups[g_id] = {'id': g_id, 'label': g_label, 'portals': [], 'total': 0}

        from collection.models import DistributionLog
        if p.output_portal_id:
            count = DistributionLog.objects.filter(
                output_portal_id=p.output_portal_id,
                status=DistributionLog.Status.SUCCESS,
            ).count()
        else:
            count = 0
        groups[g_id]['portals'].append({
            'id': p.name,
            'label': p.label,
            'decoder': p.decoder_type,
            'count': count,
        })
        groups[g_id]['total'] += count

    return list(groups.values())


@login_required
def file_registry(request):
    """File Registry page — acquisition and distribution overview."""
    acq_groups = _build_acq_tree()
    dist_groups = _build_dist_tree()
    acq_total = sum(g['total'] for g in acq_groups)
    dist_total = sum(g['total'] for g in dist_groups)

    return render(request, 'dashboard/file_registry.html', {
        'acq_groups': acq_groups,
        'acq_total': acq_total,
        'dist_groups': dist_groups,
        'dist_total': dist_total,
    })


@login_required
def file_registry_api(request):
    """File Registry API — returns paginated CDRFile records.

    Params (acquisition mode):
      source_id     filter by specific DataSource pk
      decoder_type  filter by decoder type group
      status        file status filter

    Params (distribution mode):
      dist_portal   DistributionPortal.name → resolves decoder_type; always COMPLETED
      decoder_type  fallback decoder filter

    Shared params:
      mode          'acquisition' (default) or 'distribution'
      filename      filename substring filter
      start_date / end_date  ISO date range on created_at
      page / per_page
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    mode = request.POST.get('mode', 'acquisition').strip().lower()
    dist_portal_name = request.POST.get('dist_portal', '').strip()
    source_id = request.POST.get('source_id', '').strip()
    decoder_type = request.POST.get('decoder_type', '').strip().upper()
    status_filter = request.POST.get('status', '').strip().upper()
    filename_q = request.POST.get('filename', '').strip()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()
    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 25))

    query = CDRFile.objects.select_related('source', 'uploaded_by')

    if mode == 'distribution':
        query = query.filter(status='COMPLETED')
        if dist_portal_name:
            try:
                portal = DistributionPortal.objects.get(name=dist_portal_name, enabled=True)
                query = query.filter(decoder_type=portal.decoder_type)
            except DistributionPortal.DoesNotExist:
                pass
        elif decoder_type and decoder_type != 'ALL':
            query = query.filter(decoder_type=decoder_type)
    else:
        # Acquisition
        if source_id:
            query = query.filter(source_id=source_id)
        elif decoder_type and decoder_type != 'ALL':
            query = query.filter(decoder_type=decoder_type)
        if status_filter and status_filter != 'ALL':
            query = query.filter(status=status_filter)

    if filename_q:
        query = query.filter(filename__icontains=filename_q)
    if start_date:
        try:
            query = query.filter(created_at__gte=datetime.strptime(start_date, '%Y-%m-%d'))
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(created_at__lt=end_dt)
        except ValueError:
            pass

    total = query.count()
    offset = (page - 1) * per_page
    files_qs = query.order_by('-created_at')[offset:offset + per_page]

    files = []
    for f in files_qs:
        files.append({
            'id': f.pk,
            'filename': f.filename,
            'decoder_type': f.decoder_type,
            'portal_label': _DECODER_DISPLAY.get(f.decoder_type, f.decoder_type),
            'file_size': f.file_size,
            'file_size_kb': round(f.file_size / 1024, 1) if f.file_size else 0,
            'status': f.status,
            'records_total': f.records_total,
            'records_valid': f.records_valid,
            'records_invalid': f.records_invalid,
            'records_duplicate': f.records_duplicate,
            'source': f.source.name if f.source else '-',
            'created_at': f.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'processing_started': f.processing_started.strftime('%Y-%m-%d %H:%M:%S') if f.processing_started else '',
            'processing_completed': f.processing_completed.strftime('%Y-%m-%d %H:%M:%S') if f.processing_completed else '',
            'error_message': f.error_message or '',
            'detail_url': f'/collection/files/{f.pk}/',
        })

    pages = (total + per_page - 1) // per_page

    agg = query.aggregate(
        total_size=Sum('file_size'),
        total_records=Sum('records_total'),
        total_valid=Sum('records_valid'),
        first_created=Min('created_at'),
        last_created=Max('created_at'),
    )

    return JsonResponse({
        'success': True,
        'files': files,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
        'aggregate': {
            'total_size_bytes': agg['total_size'] or 0,
            'total_size_mb': round((agg['total_size'] or 0) / (1024 * 1024), 2),
            'total_records': agg['total_records'] or 0,
            'total_valid': agg['total_valid'] or 0,
            'first_created': agg['first_created'].strftime('%Y-%m-%d %H:%M') if agg['first_created'] else '-',
            'last_created': agg['last_created'].strftime('%Y-%m-%d %H:%M') if agg['last_created'] else '-',
        },
    })


# =============================================================================
# Analytics dashboard — Network, Pair-completeness, SDP/Codec analytics
# =============================================================================

@login_required
def analytics_view(request):
    """Analytics page (Chart.js renders from /analytics/api/)."""
    return render(request, 'dashboard/analytics.html')


@login_required
def analytics_api(request):
    """JSON for the analytics dashboard: network elements + pair KPI + SDP/codec
    + time-series for charts. Range controlled by ?range=1h|6h|24h|7d|30d.
    """
    from django.db.models.functions import TruncHour, TruncDay

    # ----- Time-range parsing -----------------------------------------------
    range_map = {
        '1h':  ('hour', timedelta(hours=1)),
        '6h':  ('hour', timedelta(hours=6)),
        '24h': ('hour', timedelta(hours=24)),
        '7d':  ('day',  timedelta(days=7)),
        '30d': ('day',  timedelta(days=30)),
    }
    rng = (request.GET.get('range') or '24h').lower()
    bucket_kind, window = range_map.get(rng, range_map['24h'])
    since = timezone.now() - window
    trunc = TruncHour('created_at') if bucket_kind == 'hour' else TruncDay('created_at')

    def _series(model_cls):
        rows = (model_cls.objects
                .filter(created_at__gte=since)
                .annotate(bucket=trunc)
                .values('bucket')
                .annotate(count=Count('id'))
                .order_by('bucket'))
        return [{'t': r['bucket'].isoformat(), 'v': r['count']} for r in rows]

    series = {
        'ims':  _series(IMSRecord),
        'msc':  _series(MSCRecord),
        'pgw':  _series(PGWRecord),
        'sgsn': _series(SGSNRecord),
        'sgw':  _series(SGWRecord),
    }

    # File-processing volume time-series
    file_series = list(CDRFile.objects
                       .filter(processing_completed__gte=since,
                               status=CDRFile.Status.COMPLETED)
                       .annotate(bucket=TruncHour('processing_completed') if bucket_kind == 'hour'
                                 else TruncDay('processing_completed'))
                       .values('bucket')
                       .annotate(count=Count('id'), records=Sum('records_total'))
                       .order_by('bucket'))
    files_ts = [{'t': r['bucket'].isoformat(), 'v': r['count'], 'records': r['records'] or 0}
                for r in file_series]

    # ----- Per-stream file lifecycle time-series ----------------------------
    # Three actions × five streams.  For each, bucket by hour/day and split
    # by decoder_type so the frontend can draw multi-series line charts.
    STREAM_KEYS = ['MSC', 'IMS', 'PGW', 'SGSN', 'SGW']

    def _file_series_by_stream(qs, time_field, count_field='id', sum_field=None):
        """Bucket a CDRFile-like queryset by hour/day and group by decoder_type."""
        from django.db.models.functions import TruncHour as _TH, TruncDay as _TD
        trunc_fn = _TH(time_field) if bucket_kind == 'hour' else _TD(time_field)
        annotations = {'count': Count(count_field)}
        if sum_field:
            annotations['records'] = Sum(sum_field)
        rows = list(qs.annotate(bucket=trunc_fn)
                     .values('bucket', 'decoder_type')
                     .annotate(**annotations)
                     .order_by('bucket'))
        out = {s: [] for s in STREAM_KEYS}
        for r in rows:
            s = (r['decoder_type'] or '').upper()
            if s not in out:
                continue
            entry = {'t': r['bucket'].isoformat(), 'v': r['count']}
            if sum_field:
                entry['records'] = r.get('records') or 0
            out[s].append(entry)
        return out

    # 1. Files RECEIVED — every CDRFile created in the window
    files_received_by_stream = _file_series_by_stream(
        CDRFile.objects.filter(created_at__gte=since),
        'created_at', sum_field='records_total',
    )

    # 2. Files PROCESSED — completed files
    files_processed_by_stream = _file_series_by_stream(
        CDRFile.objects.filter(processing_completed__gte=since,
                                status=CDRFile.Status.COMPLETED),
        'processing_completed', sum_field='records_total',
    )

    # 3. Files DISTRIBUTED — successful DistributionLog rows
    from collection.models import DistributionLog
    from django.db.models.functions import TruncHour as _TH, TruncDay as _TD
    dist_trunc = _TH('delivered_at') if bucket_kind == 'hour' else _TD('delivered_at')
    dist_rows = list(DistributionLog.objects
                     .filter(delivered_at__gte=since, status=DistributionLog.Status.SUCCESS)
                     .annotate(bucket=dist_trunc)
                     .values('bucket', 'cdr_file__decoder_type')
                     .annotate(count=Count('id'), records=Sum('record_count'))
                     .order_by('bucket'))
    files_distributed_by_stream = {s: [] for s in STREAM_KEYS}
    for r in dist_rows:
        s = (r['cdr_file__decoder_type'] or '').upper()
        if s in files_distributed_by_stream:
            files_distributed_by_stream[s].append({
                't': r['bucket'].isoformat(),
                'v': r['count'],
                'records': r.get('records') or 0,
            })

    # Processing-error time-series (from ProcessingError)
    from collection.models import ProcessingError
    err_series = list(ProcessingError.objects
                      .filter(created_at__gte=since)
                      .annotate(bucket=trunc)
                      .values('bucket')
                      .annotate(count=Count('id'))
                      .order_by('bucket'))
    errors_ts = [{'t': r['bucket'].isoformat(), 'v': r['count']} for r in err_series]

    # ----- 1. Technology / RAT distribution (IMS only — most reliable source)
    tech_breakdown = (IMSRecord.objects
                      .exclude(technology='')
                      .values('technology')
                      .annotate(count=Count('id'))
                      .order_by('-count'))

    # ----- 2. Top eNodeB IDs (LTE only)
    top_enodebs = (IMSRecord.objects
                   .exclude(enodeb_id='')
                   .values('enodeb_id')
                   .annotate(count=Count('id'))
                   .order_by('-count')[:10])

    # ----- 3. Top cells (any tech)
    top_cells = (IMSRecord.objects
                 .exclude(cell_id='')
                 .values('cell_id', 'technology')
                 .annotate(count=Count('id'))
                 .order_by('-count')[:15])

    # ----- 4. Serving PLMN distribution
    plmn_breakdown = (IMSRecord.objects
                      .exclude(serving_plmn='')
                      .values('serving_plmn')
                      .annotate(count=Count('id'))
                      .order_by('-count')[:10])

    # ----- 5. Pair-completeness KPI: paired vs orphan, per stream
    def _pair_kpi(model_cls, key_field):
        keyed = model_cls.objects.exclude(**{f'{key_field}': ''}).exclude(**{f'{key_field}__isnull': True})
        total = keyed.count()
        paired = keyed.filter(paired_record__isnull=False).count()
        orphan = total - paired
        pct = round(100.0 * paired / total, 1) if total else 0
        return {
            'total_pairable': total,
            'paired': paired,
            'orphan': orphan,
            'pct': pct,
        }
    pair_kpi = {
        'IMS': _pair_kpi(IMSRecord, 'icid'),
        'MSC': _pair_kpi(MSCRecord, 'call_reference'),
    }

    # ----- 6. SDP codec analytics (IMS only)
    codec_breakdown = (IMSRecord.objects
                       .exclude(codec='')
                       .values('codec')
                       .annotate(count=Count('id'))
                       .order_by('-count')[:10])

    media_breakdown = (IMSRecord.objects
                       .exclude(media_type='')
                       .values('media_type')
                       .annotate(count=Count('id'))
                       .order_by('-count'))

    # ----- 7. SIP method distribution
    sip_method_breakdown = (IMSRecord.objects
                            .exclude(sip_method='')
                            .values('sip_method')
                            .annotate(count=Count('id'))
                            .order_by('-count')[:10])

    # ----- 8. Call type distribution (IMS VoLTE Voice / SMS / Event)
    call_type_breakdown = (IMSRecord.objects
                           .exclude(call_type='')
                           .values('call_type')
                           .annotate(count=Count('id'))
                           .order_by('-count'))

    # ----- 9. Processing health
    from collection.models import ProcessingError
    total_files = CDRFile.objects.count()
    failed_files = CDRFile.objects.filter(status=CDRFile.Status.FAILED).count()
    completed_files = CDRFile.objects.filter(status=CDRFile.Status.COMPLETED).count()
    processing_errors = ProcessingError.objects.count()

    # ----- 10. Top error classes (last 7 days)
    last_7d = timezone.now() - timedelta(days=7)
    top_errors = (ProcessingError.objects
                  .filter(created_at__gte=last_7d)
                  .values('error_class', 'stage')
                  .annotate(count=Count('id'))
                  .order_by('-count')[:10])

    return JsonResponse({
        'range':              rng,
        'bucket':             bucket_kind,
        'series':             series,
        'files_ts':           files_ts,
        'errors_ts':          errors_ts,
        'files_received_by_stream':    files_received_by_stream,
        'files_processed_by_stream':   files_processed_by_stream,
        'files_distributed_by_stream': files_distributed_by_stream,
        'tech_breakdown':     list(tech_breakdown),
        'plmn_breakdown':     list(plmn_breakdown),
        'top_enodebs':        list(top_enodebs),
        'top_cells':          list(top_cells),
        'pair_kpi':           pair_kpi,
        'codec_breakdown':    list(codec_breakdown),
        'media_breakdown':    list(media_breakdown),
        'sip_method_breakdown': list(sip_method_breakdown),
        'call_type_breakdown':  list(call_type_breakdown),
        'health': {
            'total_files':       total_files,
            'completed_files':   completed_files,
            'failed_files':      failed_files,
            'processing_errors': processing_errors,
            'failure_rate':      round(100.0 * failed_files / total_files, 1) if total_files else 0,
        },
        'top_errors': list(top_errors),
    })


# =============================================================================
# Traffic Matrix — inter-operator volume from MSC records
# =============================================================================

# Operator classification is now in core/utils/operators.py so the
# interconnect billing module can re-use it.  Keep aliases here so existing
# call sites in this file don't need to change.
from core.utils.operators import (
    classify_operator as _classify_operator,
    SL_OPERATOR_PREFIX_MAP,
)


@login_required
def traffic_matrix_view(request):
    """Traffic Matrix page — operator-to-operator call volumes."""
    return render(request, 'dashboard/traffic_matrix.html')


@login_required
def traffic_matrix_api(request):
    """Build operator-to-operator call/SMS matrices from MSC records.

    Range controlled by ?range=24h|7d|30d|all.  Returns:
      voice_matrix   - dict[operator_from][operator_to] = call count
      voice_minutes  - dict[from][to] = total duration in minutes
      sms_matrix     - dict[from][to] = SMS count
      operators      - ordered list of operators (rows/cols)
    """
    from streams.msc.models import MSCRecord
    from datetime import timedelta

    rng = (request.GET.get('range') or '24h').lower()
    window_map = {
        '24h': timedelta(hours=24),
        '7d':  timedelta(days=7),
        '30d': timedelta(days=30),
        'all': None,
    }
    window = window_map.get(rng, window_map['24h'])
    since = timezone.now() - window if window else None

    # Pull only the fields we need (avoid loading raw_data JSON)
    base_qs = MSCRecord.objects.values(
        'record_type', 'calling_number', 'called_number', 'duration'
    )
    if since:
        base_qs = base_qs.filter(start_time__gte=since)

    # Voice: MOC + MTC + GWI + GWO (one row per call leg).  Counted as ONE call
    # per (calling, called) pair to avoid double-counting MOC+MTC pairs of the
    # same Orange-to-Orange call.  Quick approximation: use MOC + (MTC - on-net)
    # so we don't double-count.  For correctness in mixed datasets we just sum
    # MOC + GWI + GWO (all distinct directions) — MTCs that have a matching
    # MOC in the dataset would otherwise double-count.
    voice_types = ['MOC', 'GWO', 'GWI']
    sms_types   = ['SMSMO', 'SMSMT']

    voice = {}        # {from_op: {to_op: count}}
    voice_minutes = {}
    sms = {}
    operators_seen = set()

    for r in base_qs.filter(record_type__in=voice_types).iterator(chunk_size=5000):
        fo = _classify_operator(r['calling_number'])
        to = _classify_operator(r['called_number'])
        operators_seen.add(fo); operators_seen.add(to)
        voice.setdefault(fo, {}).setdefault(to, 0)
        voice[fo][to] += 1
        voice_minutes.setdefault(fo, {}).setdefault(to, 0)
        voice_minutes[fo][to] += (r['duration'] or 0)

    for r in base_qs.filter(record_type__in=sms_types).iterator(chunk_size=5000):
        fo = _classify_operator(r['calling_number'])
        to = _classify_operator(r['called_number'])
        operators_seen.add(fo); operators_seen.add(to)
        sms.setdefault(fo, {}).setdefault(to, 0)
        sms[fo][to] += 1

    # Order operators with Orange first, then SL operators, then others
    SL_ORDER = ['Orange', 'Africell', 'Qcell', 'Smart', 'Sierratel',
                'Other SL', 'International', 'Short Code', 'Alphanumeric', 'Unknown']
    operators = [op for op in SL_ORDER if op in operators_seen]
    # Append any unexpected operators
    for op in sorted(operators_seen):
        if op not in operators:
            operators.append(op)

    # Voice minutes → integer minutes for display
    voice_minutes_display = {fo: {to: round(v / 60) for to, v in row.items()}
                              for fo, row in voice_minutes.items()}

    # Grand totals for the summary strip
    total_voice = sum(c for row in voice.values() for c in row.values())
    total_sms   = sum(c for row in sms.values()   for c in row.values())
    total_minutes = sum(v for row in voice_minutes.values() for v in row.values()) // 60

    # Per-row outgoing & per-column incoming totals (for KPI cards)
    by_from_voice = {op: sum(voice.get(op, {}).values()) for op in operators}
    by_to_voice   = {op: sum(row.get(op, 0) for row in voice.values()) for op in operators}
    by_from_sms   = {op: sum(sms.get(op, {}).values())   for op in operators}
    by_to_sms     = {op: sum(row.get(op, 0) for row in sms.values())   for op in operators}

    return JsonResponse({
        'range': rng,
        'operators': operators,
        'voice_matrix':   voice,
        'voice_minutes':  voice_minutes_display,
        'sms_matrix':     sms,
        'totals': {
            'voice_calls':    total_voice,
            'voice_minutes':  total_minutes,
            'sms_messages':   total_sms,
        },
        'by_from_voice':  by_from_voice,
        'by_to_voice':    by_to_voice,
        'by_from_sms':    by_from_sms,
        'by_to_sms':      by_to_sms,
    })
