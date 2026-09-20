"""Processing summary, KPIs, queue."""
from dashboard.views._common import *



@login_required
@cache_page(30)
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
def pipeline_api(request):
    """Lightweight pipeline stage counts — polled every few seconds by the dashboard."""
    qs = CDRFile.objects.exclude(status=CDRFile.Status.FAILED)
    received = qs.count()
    decoded = qs.filter(status__in=[
        CDRFile.Status.DECODED, CDRFile.Status.DISPATCHING, CDRFile.Status.COMPLETED
    ]).count()
    distributed = qs.filter(status__in=[
        CDRFile.Status.DISPATCHING, CDRFile.Status.COMPLETED
    ]).count()
    completed = qs.filter(status=CDRFile.Status.COMPLETED).count()
    active = qs.filter(status__in=[
        CDRFile.Status.COLLECTED, CDRFile.Status.PROCESSING,
        CDRFile.Status.DECODED,   CDRFile.Status.DISPATCHING,
    ]).count()
    return JsonResponse({
        'received':    received,
        'decoded':     decoded,
        'distributed': distributed,
        'completed':   completed,
        'active':      active,
    })


@login_required
@cache_page(60)
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


@staff_required
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

