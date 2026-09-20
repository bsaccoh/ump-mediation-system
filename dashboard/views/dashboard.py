"""Dashboard index, health, chart data, operator, collection."""
from dashboard.views._common import *



@login_required
def system_health_api(request):
    """API endpoint returning live Ubuntu metrics and systemd mediation service status."""
    return JsonResponse(get_system_health())


@login_required
@cache_page(30)
def dashboard_chart_data_api(request):
    """API endpoint returning chart data for the dashboard (processing volume + stream breakdown)."""
    now = timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    period = request.GET.get('period', '').strip().lower()
    days_param = request.GET.get('days')
    try:
        days = int(days_param) if days_param else 7
    except (ValueError, TypeError):
        days = 7

    selected_operator = request.GET.get('operator', '').strip()

    base_qs = CDRFile.objects.all()
    if selected_operator:
        base_qs = base_qs.filter(operator_code__iexact=selected_operator)

    labels = []
    file_counts = []
    record_counts = []

    # If period is 'today' or days <= 1, produce 24 hourly buckets across today
    if period == 'today' or days <= 1:
        for h in range(24):
            slot_start = today + timedelta(hours=h)
            slot_end = slot_start + timedelta(hours=1)
            labels.append(slot_start.strftime('%H:%M'))
            slot_files = base_qs.filter(created_at__gte=slot_start, created_at__lt=slot_end)
            file_counts.append(slot_files.count())
            agg = slot_files.aggregate(t=Sum('records_total'))
            record_counts.append(agg['t'] or 0)
    else:
        # Multi-day view (e.g. exactly 7 or 30 days)
        for i in range(days - 1, -1, -1):
            day_start = today - timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            labels.append(day_start.strftime('%b %d'))
            day_files = base_qs.filter(created_at__gte=day_start, created_at__lt=day_end)
            file_counts.append(day_files.count())
            agg = day_files.aggregate(t=Sum('records_total'))
            record_counts.append(agg['t'] or 0)

    # Stream breakdown from CDRFile.decoder_type
    stream_stats = (
        base_qs
        .exclude(decoder_type='')
        .values('decoder_type')
        .annotate(total=Sum('records_total'))
        .order_by('-total')
    )
    stream_labels = []
    stream_values = []
    grand_total = sum(s['total'] or 0 for s in stream_stats) or 1
    for s in stream_stats:
        stream_labels.append((s['decoder_type'] or 'Other').upper())
        stream_values.append(round((s['total'] or 0) / grand_total * 100))

    # File status breakdown
    status_counts = (
        base_qs
        .values('status')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    status_labels = []
    status_values = []
    total_files_count = sum(s['count'] for s in status_counts) or 1
    for s in status_counts:
        status_labels.append(s['status'].replace('_', ' ').title())
        status_values.append(round(s['count'] / total_files_count * 100))

    return JsonResponse({
        'processing': {
            'labels': labels,
            'files': file_counts,
            'records': record_counts,
        },
        'streams': {
            'labels': stream_labels,
            'values': stream_values,
        },
        'file_status': {
            'labels': status_labels,
            'values': status_values,
        },
    })


@login_required
def index(request):
    """Main dashboard with overview statistics and real-time operational state."""
    now = timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # --- Filters from query params ---
    selected_operator = request.GET.get('operator', '').strip()
    period = request.GET.get('period', 'today').strip()

    period_days = {'today': 0, '7': 7, '30': 30}
    days = period_days.get(period, 0)
    period_start = today - timedelta(days=days)
    prev_start = period_start - timedelta(days=max(days, 1))
    prev_end = period_start

    # Base queryset with operator filter
    base_qs = CDRFile.objects.all()
    if selected_operator:
        base_qs = base_qs.filter(operator_code__iexact=selected_operator)

    # Operator list (always unfiltered)
    operator_list = list(
        CDRFile.objects.exclude(operator_code='')
        .values_list('operator_code', flat=True)
        .distinct()
        .order_by('operator_code')
    )

    # --- KPI stats for the selected period ---
    total_files = base_qs.count()
    current_qs = base_qs.filter(created_at__gte=period_start)
    prev_qs = base_qs.filter(created_at__gte=prev_start, created_at__lt=prev_end)

    files_received_val = current_qs.count()
    files_received_prev = prev_qs.count()

    files_completed = base_qs.filter(status=CDRFile.Status.COMPLETED).count()
    files_completed_current = current_qs.filter(status=CDRFile.Status.COMPLETED).count()
    files_completed_prev = prev_qs.filter(status=CDRFile.Status.COMPLETED).count()
    files_processed_val = files_completed_current

    files_failed = base_qs.filter(status=CDRFile.Status.FAILED).count()
    files_pending = base_qs.filter(
        status__in=[CDRFile.Status.PENDING, CDRFile.Status.PROCESSING, CDRFile.Status.COLLECTED]
    ).count()

    agg_current = current_qs.aggregate(t=Sum('records_total'))
    records_current = agg_current['t'] or 0
    agg_prev = prev_qs.aggregate(t=Sum('records_total'))
    records_prev = agg_prev['t'] or 0
    records_processed_val = records_current

    agg_total = base_qs.aggregate(t=Sum('records_total'))
    total_records = agg_total['t'] or 0

    success_rate_val = round((files_completed / total_files * 100), 1) if total_files > 0 else 0.0

    # Change percentages (current period vs previous period)
    def _pct_change(current, previous):
        if previous and previous > 0:
            change = round(((current - previous) / previous) * 100)
            return f"+{change}%" if change >= 0 else f"{change}%"
        if current > 0:
            return "+100%"
        return ""

    files_received_change = _pct_change(files_received_val, files_received_prev)
    files_processed_change = _pct_change(files_completed_current, files_completed_prev)
    records_processed_change = _pct_change(records_current, records_prev)

    # Avg decode time
    avg_decode_time_val = "--"
    try:
        duration_qs = base_qs.filter(
            processing_started__isnull=False,
            processing_completed__isnull=False,
            status=CDRFile.Status.COMPLETED
        )
        if duration_qs.exists():
            durations = [
                f.processing_duration for f in duration_qs[:50]
                if f.processing_duration is not None and f.processing_duration > 0
            ]
            if durations:
                avg_decode_time_val = f"{round(sum(durations) / len(durations), 1)}s"
    except Exception:
        logger.debug("Could not compute avg decode time", exc_info=True)

    # Recent files
    recent_files = []
    for rf in base_qs.select_related('source').order_by('-created_at')[:5]:
        op_name = (rf.operator_code or (rf.source.name if rf.source else 'Unknown')).capitalize()
        source_code = rf.network_element.upper() if rf.network_element else (rf.source.name[:6] if rf.source else '--')
        dtype = (rf.decoder_type or '--').upper()
        type_class = f"type-{dtype.lower()}" if dtype.lower() in ['pgw', 'msc', 'ims', 'ocs'] else 'type-pgw'
        st = rf.get_status_display()
        st_class = "processed" if rf.status == CDRFile.Status.COMPLETED else rf.status.lower()
        rec = f"{rf.records_total:,}" if rf.records_total else "0"
        rec_time = rf.created_at.strftime('%Y-%m-%d %H:%M') if rf.created_at else ''
        recent_files.append({
            'filename': rf.filename,
            'operator': op_name,
            'source': source_code,
            'type': dtype,
            'type_class': type_class,
            'records': rec,
            'status': st,
            'status_class': st_class,
            'received': rec_time,
        })

    # Alarms
    alarms = []
    for al in Alert.objects.order_by('-timestamp')[:5]:
        sev = al.get_severity_display()
        sev_class = al.severity.lower()
        t_str = al.timestamp.strftime('%Y-%m-%d %H:%M') if al.timestamp else ''
        alarms.append({
            'time': t_str,
            'severity': sev,
            'severity_class': sev_class,
            'source': al.source or 'System',
            'message': al.message,
        })

    # Pipeline stages — cumulative throughput (files that reached each stage)
    files_received = base_qs.exclude(status=CDRFile.Status.FAILED).count()
    files_decoded_total = base_qs.filter(
        status__in=[CDRFile.Status.DECODED, CDRFile.Status.DISPATCHING, CDRFile.Status.COMPLETED]
    ).count()
    files_distributed_total = base_qs.filter(
        status__in=[CDRFile.Status.DISPATCHING, CDRFile.Status.COMPLETED]
    ).count()
    pipeline_stages = [
        {'name': 'Collect', 'icon': 'bi-folder-fill', 'color': 'blue', 'count': files_received, 'label': 'Received'},
        {'name': 'Decode', 'icon': 'bi-gear-fill', 'color': 'blue', 'count': files_decoded_total, 'label': 'Decoded'},
        {'name': 'Distribute', 'icon': 'bi-share-fill', 'color': 'green', 'count': files_distributed_total, 'label': 'Distributed'},
        {'name': 'Completed', 'icon': 'bi-check-lg', 'color': 'green', 'count': files_completed, 'label': 'Files'},
    ]

    # Top Operators (always unfiltered to show comparison)
    op_stats = (
        CDRFile.objects
        .exclude(operator_code='')
        .values('operator_code')
        .annotate(total=Sum('records_total'))
        .order_by('-total')[:5]
    )
    top_operators = []
    if op_stats:
        max_records = op_stats[0]['total'] or 1
        for op in op_stats:
            total = op['total'] or 0
            top_operators.append({
                'name': (op['operator_code'] or 'Unknown').capitalize(),
                'records': f"{total:,}",
                'percent': round(total / max_records * 100),
            })

    system_health = get_system_health()
    alert_count = Alert.objects.count()

    prev_label = "vs previous period" if days > 0 else "vs yesterday"

    return render(request, 'dashboard/index.html', {
        'files_received': files_received_val,
        'files_received_change': files_received_change,
        'files_processed': files_processed_val,
        'files_processed_change': files_processed_change,
        'records_processed': f"{records_processed_val:,}",
        'records_processed_change': records_processed_change,
        'success_rate': f"{int(success_rate_val)}%" if success_rate_val == int(success_rate_val) else f"{success_rate_val}%",
        'avg_decode_time': avg_decode_time_val,
        'prev_label': prev_label,
        'date_start': period_start.strftime('%Y-%m-%d'),
        'date_end': now.strftime('%Y-%m-%d'),
        'operators': [op.capitalize() for op in operator_list],
        'selected_operator': selected_operator,
        'selected_period': period,
        'recent_files': recent_files,
        'alarms': alarms,
        'alert_count': alert_count,
        'pipeline_stages': pipeline_stages,
        'top_operators': top_operators,
        'system_health': system_health,
        'total_files': total_files,
        'files_completed': files_completed,
        'files_failed': files_failed,
        'files_pending': files_pending,
        'total_records': total_records,
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

