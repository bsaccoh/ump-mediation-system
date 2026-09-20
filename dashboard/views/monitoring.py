"""System and flow monitoring."""
from dashboard.views._common import *


# ==============================================================================
# SYSTEM MONITORING (MASTER REFERENCE EXACT IMPLEMENTATION)
# ==============================================================================

@operator_required
def system_monitoring_view(request):
    """System Monitoring dashboard matching the master reference design pixel-for-pixel."""
    from dashboard.services.monitoring_service import (
        get_top_kpis,
        get_system_health_status,
        get_hardware_gauges,
        get_recent_processing_activity,
        get_recent_errors,
        ensure_recent_snapshot,
        get_mediation_flow_summary,
    )

    selected_op = request.GET.get('operator') or request.session.get('active_operator', '')
    if selected_op.upper() in ('ALL', 'ALL OPERATORS', ''):
        selected_op = ''

    ensure_recent_snapshot()
    kpis = get_top_kpis(time_range='24h', operator_code=selected_op)
    health = get_system_health_status()
    gauges = get_hardware_gauges()
    recent_activity = get_recent_processing_activity(limit=7, operator_code=selected_op)
    recent_errors = get_recent_errors(limit=6, operator_code=selected_op)
    flow_summary = get_mediation_flow_summary(range_key='24h', operator=selected_op)
    now_str = timezone.now().strftime('%b %d, %Y %H:%M:%S')

    context = {
        'kpis': kpis,
        'health': health,
        'gauges': gauges,
        'recent_activity': recent_activity,
        'recent_errors': recent_errors,
        'recent_errors_count': len(recent_errors),
        'flow': flow_summary,
        'last_updated': now_str,
        'selected_operator': selected_op,
    }
    return render(request, 'dashboard/monitoring.html', context)


@operator_required
def system_monitoring_overview_api(request):
    """API endpoint returning live top KPIs, health status, gauges, activity, and errors."""
    from dashboard.services.monitoring_service import (
        get_top_kpis,
        get_system_health_status,
        get_hardware_gauges,
        get_recent_processing_activity,
        get_recent_errors,
        ensure_recent_snapshot,
    )

    selected_op = request.GET.get('operator') or request.session.get('active_operator', '')
    if selected_op.upper() in ('ALL', 'ALL OPERATORS', ''):
        selected_op = ''

    time_range = request.GET.get('range', '24h')
    ensure_recent_snapshot()
    kpis = get_top_kpis(time_range=time_range, operator_code=selected_op)
    health = get_system_health_status()
    gauges = get_hardware_gauges()
    recent_activity = get_recent_processing_activity(limit=7, operator_code=selected_op)
    recent_errors = get_recent_errors(limit=6, operator_code=selected_op)
    now_str = timezone.now().strftime('%b %d, %Y %H:%M:%S')

    return JsonResponse({
        'kpis': kpis,
        'health': health,
        'gauges': gauges,
        'recent_activity': recent_activity,
        'recent_errors': recent_errors,
        'recent_errors_count': len(recent_errors),
        'last_updated': now_str,
    })


@operator_required
def system_monitoring_timeseries_api(request):
    """API endpoint returning time-series data for all charts."""
    from dashboard.services.monitoring_service import get_timeseries_data

    selected_op = request.GET.get('operator') or request.session.get('active_operator', '')
    if selected_op.upper() in ('ALL', 'ALL OPERATORS', ''):
        selected_op = ''

    time_range = request.GET.get('range', '24h')
    data = get_timeseries_data(time_range=time_range, operator_code=selected_op)
    return JsonResponse(data)


@operator_required
def system_monitoring_error_detail_api(request, error_id):
    """Safe diagnostic endpoint returning non-sensitive details for a recent error."""
    if error_id.startswith('pe_'):
        try:
            pe_id = int(error_id.replace('pe_', ''))
            pe = ProcessingError.objects.select_related('cdr_file').get(id=pe_id)
            return JsonResponse({
                'id': error_id,
                'time': pe.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'component': f"Decoder ({pe.stage})",
                'severity': 'Critical' if 'fatal' in pe.error_message.lower() else 'Major',
                'message': pe.error_message,
                'filename': pe.cdr_file.filename if pe.cdr_file else '--',
                'stage': pe.stage,
                'error_class': pe.error_class or 'DecoderException',
                'retry_count': pe.cdr_file.retry_count if pe.cdr_file else 0,
            })
        except Exception:
            logger.debug("Could not fetch processing error pe_%s", error_id, exc_info=True)
    elif error_id.startswith('cf_'):
        try:
            cf_id = int(error_id.replace('cf_', ''))
            cf = CDRFile.objects.get(id=cf_id)
            return JsonResponse({
                'id': error_id,
                'time': cf.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'component': f"{cf.decoder_type.upper() if cf.decoder_type else 'Decoder'}",
                'severity': 'Critical',
                'message': cf.error_message or 'Processing failure encountered on input file.',
                'filename': cf.filename,
                'stage': cf.get_status_display(),
                'error_class': 'FileProcessingError',
                'retry_count': cf.retry_count,
            })
        except Exception:
            logger.debug("Could not fetch CDR file error cf_%s", error_id, exc_info=True)
    elif error_id.startswith('al_'):
        try:
            al_id = int(error_id.replace('al_', ''))
            al = Alert.objects.get(id=al_id)
            return JsonResponse({
                'id': error_id,
                'time': al.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'component': al.source or al.category or 'System',
                'severity': al.get_severity_display(),
                'message': al.message,
                'filename': '--',
                'stage': al.category,
                'error_class': 'SystemAlarm',
                'retry_count': 0,
            })
        except Exception:
            logger.debug("Could not fetch alert error al_%s", error_id, exc_info=True)

    return JsonResponse({'error': 'Error details not found'}, status=404)


# ==============================================================================
# MEDIATION FLOW MONITORING APIS
# ==============================================================================

@operator_required
def flow_monitoring_summary_api(request):
    """Unified API endpoint returning all 4 components of Mediation Flow Monitoring."""
    from dashboard.services.monitoring_service import get_mediation_flow_summary
    range_key = request.GET.get('range', '24h')
    operator = request.GET.get('operator') or request.session.get('active_operator', '')
    if operator.upper() in ('ALL', 'ALL OPERATORS', ''):
        operator = ''
    col_group = request.GET.get('col_group', 'portal')
    proc_group = request.GET.get('proc_group', 'stream')
    dist_group = request.GET.get('dist_group', 'downstream')

    data = get_mediation_flow_summary(
        range_key=range_key,
        operator=operator,
        col_group=col_group,
        proc_group=proc_group,
        dist_group=dist_group,
    )
    return JsonResponse(data)


@operator_required
def flow_collection_api(request):
    """API endpoint returning Collection Volume data (Group by portal or stream)."""
    from dashboard.services.monitoring_service import parse_time_range, get_flow_collection_data
    range_key = request.GET.get('range', '24h')
    operator = request.GET.get('operator') or request.session.get('active_operator', '')
    if operator.upper() in ('ALL', 'ALL OPERATORS', ''):
        operator = ''
    group_by = request.GET.get('group_by', 'portal')
    start_dt, end_dt, _, _, _, _ = parse_time_range(range_key)
    data = get_flow_collection_data(start_dt, end_dt, operator=operator, group_by=group_by)
    return JsonResponse(data)


@operator_required
def flow_processing_api(request):
    """API endpoint returning Processing Volume data (Group by stream)."""
    from dashboard.services.monitoring_service import parse_time_range, get_flow_processing_data
    range_key = request.GET.get('range', '24h')
    operator = request.GET.get('operator') or request.session.get('active_operator', '')
    if operator.upper() in ('ALL', 'ALL OPERATORS', ''):
        operator = ''
    group_by = request.GET.get('group_by', 'stream')
    start_dt, end_dt, _, _, _, _ = parse_time_range(range_key)
    data = get_flow_processing_data(start_dt, end_dt, operator=operator, group_by=group_by)
    return JsonResponse(data)


@operator_required
def flow_distribution_api(request):
    """API endpoint returning Distribution Volume data (Group by downstream, output_portal, or stream)."""
    from dashboard.services.monitoring_service import parse_time_range, get_flow_distribution_data
    range_key = request.GET.get('range', '24h')
    operator = request.GET.get('operator') or request.session.get('active_operator', '')
    if operator.upper() in ('ALL', 'ALL OPERATORS', ''):
        operator = ''
    group_by = request.GET.get('group_by', 'downstream')
    start_dt, end_dt, _, _, _, _ = parse_time_range(range_key)
    data = get_flow_distribution_data(start_dt, end_dt, operator=operator, group_by=group_by)
    return JsonResponse(data)


@operator_required
def flow_reconciliation_api(request):
    """API endpoint returning End-to-End and Per-Stream Reconciliation data."""
    from dashboard.services.monitoring_service import parse_time_range, get_flow_reconciliation_data
    range_key = request.GET.get('range', '24h')
    operator = request.GET.get('operator') or request.session.get('active_operator', '')
    if operator.upper() in ('ALL', 'ALL OPERATORS', ''):
        operator = ''
    start_dt, end_dt, _, _, _, _ = parse_time_range(range_key)
    range_titles = {
        '1h': 'Last 1 Hour',
        '6h': 'Last 6 Hours',
        '12h': 'Last 12 Hours',
        '24h': 'Last 24 Hours',
        '7d': 'Last 7 Days',
        '30d': 'Last 30 Days',
    }
    label = range_titles.get(range_key, 'Last 24 Hours')
    data = get_flow_reconciliation_data(start_dt, end_dt, operator=operator, time_label=label)
    return JsonResponse(data)


@operator_required
def backlog_api(request):
    """API endpoint returning backlog data for all three backlog types."""
    from dashboard.services.monitoring_service import get_backlog_data
    operator = request.GET.get('operator') or request.session.get('active_operator', '')
    if operator.upper() in ('ALL', 'ALL OPERATORS', ''):
        operator = ''
    data = get_backlog_data(operator=operator)
    return JsonResponse(data)


@operator_required
def storage_api(request):
    """API endpoint returning storage utilisation for configured UMP directories."""
    from dashboard.services.monitoring_service import get_storage_data
    data = get_storage_data()
    return JsonResponse({'locations': data})


@operator_required
def active_alarms_api(request):
    """API endpoint returning active (unacknowledged) alarms."""
    alarms = Alert.objects.filter(acknowledged=False).order_by('-severity', '-timestamp')[:50]
    data = [{
        'id': a.id,
        'severity': a.severity,
        'category': a.category,
        'source': a.source,
        'message': a.message,
        'timestamp': a.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
    } for a in alarms]
    return JsonResponse({'alarms': data, 'count': len(data)})


