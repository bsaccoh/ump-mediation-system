"""Dashboard views."""
import csv
import io
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404
from django.db.models import Count, Sum, Avg, Q, F, Min, Max
from django.utils import timezone

from collection.models import CDRFile, DataSource
from streams.msc.models import MSCRecord
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


# =============================================================================
# Unified CDR Search
# =============================================================================

STREAM_MODELS = {
    'MSC': MSCRecord,
    'PGW': PGWRecord,
    'SGSN': SGSNRecord,
    'SGW': SGWRecord,
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
    if imsi:
        query = query.filter(imsi__icontains=imsi)
        filters.append(f'imsi={imsi}')
    if imei:
        query = query.filter(imei__icontains=imei)
        filters.append(f'imei={imei}')

    # MSC-specific filters
    if stream == 'MSC':
        if service_type:
            query = query.filter(service_type=service_type)
            filters.append(f'service={service_type}')
        if record_type:
            variant_groups = {
                'SMSMT': ['SMSMT', 'SIP_SMSMT', 'SMSMT_GW'],
                'SMSMO': ['SMSMO', 'SIP_SMSMO', 'SMSMO_IW'],
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
    msc_count = MSCRecord.objects.count()
    pgw_count = PGWRecord.objects.count()
    sgsn_count = SGSNRecord.objects.count()
    sgw_count = SGWRecord.objects.count()
    sources = DataSource.objects.filter(enabled=True).order_by('name')

    return render(request, 'dashboard/cdr_search.html', {
        'msc_count': msc_count,
        'pgw_count': pgw_count,
        'sgsn_count': sgsn_count,
        'sgw_count': sgw_count,
        'total_records': msc_count + pgw_count + sgsn_count + sgw_count,
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
                'cell_id': rec.cell_id,
                'lac': rec.lac,
                'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                'duration': rec.duration,
                'originating_trunk': rec.originating_trunk,
                'terminating_trunk': rec.terminating_trunk,
                'result_code': rec.result_code,
                'rat_type': rec.rat_type,
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

MSC_EXPORT_COLUMNS = [
    ('record_type', 'Record Type'), ('service_type', 'Service Type'),
    ('call_direction', 'Direction'), ('calling_number', 'Calling Number'),
    ('called_number', 'Called Number'), ('dialed_number', 'Dialed Number'),
    ('charged_msisdn', 'Charged MSISDN'), ('imsi', 'IMSI'), ('imei', 'IMEI'),
    ('start_time', 'Start Time'), ('end_time', 'End Time'), ('duration', 'Duration (s)'),
    ('msc_id', 'MSC ID'), ('cell_id', 'Cell ID'), ('lac', 'LAC'),
    ('rat_type', 'RAT Type'), ('originating_trunk', 'Originating Trunk'),
    ('terminating_trunk', 'Terminating Trunk'), ('teleservice_code', 'Teleservice Code'),
    ('result_code', 'Result Code'), ('roaming_indicator', 'Roaming'),
    ('network_record_id', 'Network Record ID'), ('call_reference', 'Call Reference'),
    ('status', 'Status'),
]

PGW_EXPORT_COLUMNS = [
    ('record_type', 'Record Type'), ('calling_number', 'MSISDN'), ('apn', 'APN'),
    ('imsi', 'IMSI'), ('imei', 'IMEI'), ('start_time', 'Start Time'),
    ('end_time', 'End Time'), ('duration', 'Duration (s)'),
    ('data_volume_up', 'Upload (bytes)'), ('data_volume_down', 'Download (bytes)'),
    ('rat_type', 'RAT Type'), ('pdn_type', 'PDN Type'),
    ('pgw_address', 'PGW Address'), ('sgw_address', 'SGW Address'),
    ('node_id', 'Node ID'), ('cell_id', 'Cell ID'), ('lac', 'TAC'),
    ('serving_plmn', 'Serving PLMN'), ('cause_for_closing', 'Cause'),
    ('is_roaming', 'Roaming'), ('charging_id', 'Charging ID'), ('status', 'Status'),
]

SGSN_EXPORT_COLUMNS = [
    ('record_type', 'Record Type'), ('service_type', 'Service Type'),
    ('calling_number', 'MSISDN'), ('apn', 'APN'),
    ('imsi', 'IMSI'), ('imei', 'IMEI'), ('start_time', 'Start Time'),
    ('end_time', 'End Time'), ('duration', 'Duration (s)'),
    ('data_volume_up', 'Upload (bytes)'), ('data_volume_down', 'Download (bytes)'),
    ('rat_type', 'RAT Type'), ('pdp_type', 'PDP Type'),
    ('sgsn_address', 'SGSN Address'), ('ggsn_address', 'GGSN Address'),
    ('node_id', 'Node ID'), ('cell_id', 'Cell ID'), ('lac', 'LAC'), ('rac', 'RAC'),
    ('serving_plmn', 'Serving PLMN'), ('cause_for_closing', 'Cause'),
    ('is_roaming', 'Roaming'), ('charging_id', 'Charging ID'), ('status', 'Status'),
]

SGW_EXPORT_COLUMNS = [
    ('record_type', 'Record Type'), ('calling_number', 'MSISDN'), ('apn', 'APN'),
    ('imsi', 'IMSI'), ('imei', 'IMEI'), ('start_time', 'Start Time'),
    ('end_time', 'End Time'), ('duration', 'Duration (s)'),
    ('data_volume_up', 'Upload (bytes)'), ('data_volume_down', 'Download (bytes)'),
    ('rat_type', 'RAT Type'), ('pdn_type', 'PDN Type'),
    ('sgw_address', 'SGW Address'), ('pgw_address', 'PGW Address'),
    ('node_id', 'Node ID'), ('cell_id', 'Cell ID'), ('lac', 'TAC'),
    ('serving_plmn', 'Serving PLMN'), ('cause_for_closing', 'Cause'),
    ('is_roaming', 'Roaming'), ('charging_id', 'Charging ID'), ('status', 'Status'),
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
        return render(request, 'dashboard/cdr_detail.html', {'record': record, 'stream': 'MSC'})
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
    """Subscriber timeline API — returns all activity for a MSISDN."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    msisdn = request.POST.get('msisdn', '').strip()
    if not msisdn or len(msisdn) < 5:
        return JsonResponse({'success': False, 'message': 'Enter a valid MSISDN (min 5 digits).'})

    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 30))

    # Search across both MSC and PGW
    msc_query = MSCRecord.objects.filter(
        Q(calling_number__icontains=msisdn) |
        Q(called_number__icontains=msisdn) |
        Q(charged_msisdn__icontains=msisdn)
    ).select_related('file', 'source')

    pgw_query = PGWRecord.objects.filter(
        Q(calling_number__icontains=msisdn)
    ).select_related('file', 'source')

    msc_total = msc_query.count()
    pgw_total = pgw_query.count()
    total = msc_total + pgw_total

    if total == 0:
        return JsonResponse({
            'success': True, 'records': [], 'summary': {},
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'message': f'No activity found for "{msisdn}". Try a partial number.',
        })

    # Summary from MSC
    msc_summary = msc_query.aggregate(
        total_voice=Count('id', filter=Q(service_type='VOICE')),
        total_sms=Count('id', filter=Q(service_type='SMS')),
        total_duration=Sum('duration'),
        avg_duration=Avg('duration', filter=Q(service_type='VOICE')),
    )

    # Summary from PGW
    # Aggregate data_volume_up and data_volume_down as integers in Python
    pgw_data_volumes = pgw_query.values_list('data_volume_up', 'data_volume_down')
    pgw_total_data_up = sum(int(x[0]) if x[0] and str(x[0]).strip() else 0 for x in pgw_data_volumes)
    pgw_total_data_down = sum(int(x[1]) if x[1] and str(x[1]).strip() else 0 for x in pgw_data_volumes)
    pgw_summary = {
        'total_data': pgw_query.count(),
        'total_data_up': pgw_total_data_up,
        'total_data_down': pgw_total_data_down,
    }

    # Date range (from both)
    msc_dates = msc_query.aggregate(first=Min('start_time'), last=Max('start_time'))
    pgw_dates = pgw_query.aggregate(first=Min('start_time'), last=Max('start_time'))

    first_dates = [d for d in [msc_dates.get('first'), pgw_dates.get('first')] if d]
    last_dates = [d for d in [msc_dates.get('last'), pgw_dates.get('last')] if d]
    first_activity = min(first_dates) if first_dates else None
    last_activity = max(last_dates) if last_dates else None

    # Primary IMSI/IMEI from MSC
    imsi_qs = (msc_query.exclude(imsi='').values('imsi')
               .annotate(cnt=Count('id')).order_by('-cnt')[:1])
    imei_qs = (msc_query.exclude(imei='').values('imei')
               .annotate(cnt=Count('id')).order_by('-cnt')[:1])

    total_data_bytes = (pgw_summary['total_data_up'] or 0) + (pgw_summary['total_data_down'] or 0)

    summary = {
        'msisdn': msisdn,
        'total_records': total,
        'total_voice': msc_summary['total_voice'] or 0,
        'total_sms': msc_summary['total_sms'] or 0,
        'total_data_sessions': pgw_summary['total_data'] or 0,
        'total_duration': msc_summary['total_duration'] or 0,
        'avg_duration': round(msc_summary['avg_duration'] or 0, 1),
        'total_data_mb': round(total_data_bytes / (1024 * 1024), 2) if total_data_bytes else 0,
        'first_activity': first_activity.strftime('%Y-%m-%d %H:%M') if first_activity else '-',
        'last_activity': last_activity.strftime('%Y-%m-%d %H:%M') if last_activity else '-',
        'primary_imsi': imsi_qs[0]['imsi'] if imsi_qs else '-',
        'primary_imei': imei_qs[0]['imei'] if imei_qs else '-',
    }

    # Combine and paginate — MSC records + PGW records, sorted by start_time desc
    # For simplicity, paginate MSC first then PGW
    offset = (page - 1) * per_page
    records = []

    # Build a combined list (MSC + PGW) ordered by start_time desc
    msc_recs = list(msc_query.order_by('-start_time', '-created_at')[:500])
    pgw_recs = list(pgw_query.order_by('-start_time', '-created_at')[:500])

    combined = []
    for rec in msc_recs:
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
            'status': rec.status,
        })

    for rec in pgw_recs:
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
            'status': rec.status,
            'data_volume_mb': rec.data_volume_mb,
        })

    # Sort combined by start_time desc
    combined.sort(key=lambda r: r.get('start_time_dt') or datetime.min, reverse=True)

    # Paginate
    page_records = combined[offset:offset + per_page]
    # Remove sort key
    for r in page_records:
        r.pop('start_time_dt', None)

    pages = (total + per_page - 1) // per_page

    return JsonResponse({
        'success': True,
        'records': page_records,
        'summary': summary,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
    })
