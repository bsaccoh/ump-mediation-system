"""PGW CDR search, export, and detail views."""
import csv
import io
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404
from django.db.models import Q, Sum, Avg, Count

from .models import PGWRecord
from collection.models import DataSource

RAT_TYPE_NAMES = {
    '1': 'UTRAN', '2': 'GERAN', '3': 'WLAN', '4': 'GAN',
    '5': 'HSPA_Evolution', '6': 'EUTRAN', '7': 'Virtual',
    '8': 'EUTRAN_NB_IoT', '9': 'LTE_M', '10': 'NR',
}


# =============================================================================
# Shared filter builder
# =============================================================================

def _build_pgw_queryset(params):
    """Build filtered PGWRecord queryset from request params dict."""
    msisdn = params.get('calling_number', '').strip()
    apn = params.get('apn', '').strip()
    imsi = params.get('imsi', '').strip()
    imei = params.get('imei', '').strip()
    lac = params.get('lac', '').strip()
    node_id = params.get('node_id', '').strip()
    record_type = params.get('record_type', '').strip()
    rat_type = params.get('rat_type', '').strip()
    source_id = params.get('source_id', '').strip()
    start_date = params.get('start_date', '').strip()
    end_date = params.get('end_date', '').strip()

    query = PGWRecord.objects.select_related('file', 'source')
    filters = []

    if msisdn:
        query = query.filter(calling_number__icontains=msisdn)
        filters.append(f'msisdn={msisdn}')
    if apn:
        query = query.filter(apn__icontains=apn)
        filters.append(f'apn={apn}')
    if imsi:
        query = query.filter(imsi__icontains=imsi)
        filters.append(f'imsi={imsi}')
    if imei:
        query = query.filter(imei__icontains=imei)
        filters.append(f'imei={imei}')
    if lac:
        query = query.filter(lac__icontains=lac)
        filters.append(f'lac={lac}')
    if node_id:
        query = query.filter(node_id__icontains=node_id)
        filters.append(f'node_id={node_id}')
    if record_type:
        types = [t.strip() for t in record_type.split(',') if t.strip()]
        query = query.filter(record_type__in=types)
        filters.append(f'type={",".join(types)}')
    if rat_type:
        query = query.filter(rat_type=rat_type)
        filters.append(f'rat={rat_type}')
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


# =============================================================================
# Search views
# =============================================================================

@login_required
def pgw_search(request):
    """PGW CDR search page."""
    total_records = PGWRecord.objects.count()
    sources = DataSource.objects.filter(enabled=True).order_by('name')

    # APN distribution for filter dropdown
    apn_counts = (PGWRecord.objects.exclude(apn='')
                  .values('apn').annotate(count=Count('id'))
                  .order_by('-count')[:20])

    return render(request, 'dashboard/pgw_search.html', {
        'total_records': total_records,
        'sources': sources,
        'apn_list': apn_counts,
    })


@login_required
def pgw_search_api(request):
    """PGW CDR search API endpoint (POST)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 20))

    query, _ = _build_pgw_queryset(request.POST)
    total = query.count()

    if total == 0:
        total_in_db = PGWRecord.objects.count()
        if total_in_db > 0:
            apn_counts = (PGWRecord.objects.values('apn')
                          .annotate(count=Count('id'))
                          .order_by('-count')[:5])
            apn_summary = ', '.join(f"{a['apn']}: {a['count']:,}" for a in apn_counts)
            message = f'No records match. ({total_in_db:,} in DB: {apn_summary}). Try Clear then Search.'
        else:
            message = 'No PGW records. Upload and process PGW CDR files first.'

        return JsonResponse({
            'success': True, 'records': [],
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'stats': {'total_records': 0, 'total_data_up': 0, 'total_data_down': 0},
            'message': message,
        })

    offset = (page - 1) * per_page
    records_qs = query.order_by('-created_at')[offset:offset + per_page]

    records = []
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
            'lac': rec.lac,
            'tac': rec.tac,
            'eci': rec.eci,
            'rating_group': rec.rating_group or '',
            'status': rec.status,
        })

    # Aggregate data_volume_up and data_volume_down as integers in Python
    data_volumes = query.values_list('data_volume_up', 'data_volume_down')
    total_data_up = sum(int(x[0]) if x[0] and str(x[0]).strip() else 0 for x in data_volumes)
    total_data_down = sum(int(x[1]) if x[1] and str(x[1]).strip() else 0 for x in data_volumes)
    avg_duration = query.aggregate(avg_duration=Avg('duration'))['avg_duration'] or 0
    pages = (total + per_page - 1) // per_page

    total_bytes = total_data_up + total_data_down

    return JsonResponse({
        'success': True,
        'records': records,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
        'stats': {
            'total_records': total,
            'total_data_up': total_data_up,
            'total_data_down': total_data_down,
            'total_data_mb': round(total_bytes / (1024 * 1024), 2),
            'avg_duration': round(avg_duration, 2),
        },
    })


# =============================================================================
# Export
# =============================================================================

EXPORT_COLUMNS = [
    ('record_type', 'Record Type'),
    ('calling_number', 'MSISDN'),
    ('apn', 'APN'),
    ('imsi', 'IMSI'),
    ('imei', 'IMEI'),
    ('start_time', 'Start Time'),
    ('end_time', 'End Time'),
    ('duration', 'Duration (s)'),
    ('data_volume_up', 'Upload (bytes)'),
    ('data_volume_down', 'Download (bytes)'),
    ('rat_type', 'RAT Type'),
    ('pdn_type', 'PDN Type'),
    ('pgw_address', 'PGW Address'),
    ('sgw_address', 'SGW Address'),
    ('node_id', 'Node ID'),
    ('cell_id', 'Cell ID'),
    ('lac', 'LAC'),
    ('tac', 'TAC'),
    ('eci', 'ECI'),
    ('serving_plmn', 'Serving PLMN'),
    ('cause_for_closing', 'Cause'),
    ('is_roaming', 'Roaming'),
    ('charging_id', 'Charging ID'),
    ('rating_group', 'Rating Group'),
    ('status', 'Status'),
]

MAX_EXPORT_ROWS = 100000


@login_required
def pgw_export(request):
    """Export filtered PGW records as CSV."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    query, filter_desc = _build_pgw_queryset(request.POST)
    total = query.count()

    if total > MAX_EXPORT_ROWS:
        return JsonResponse({
            'error': f'Too many records ({total:,}). Maximum export is {MAX_EXPORT_ROWS:,}. Add more filters.'
        }, status=400)

    if total == 0:
        return JsonResponse({'error': 'No records match the filters.'}, status=404)

    def stream_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)

        writer.writerow([col[1] for col in EXPORT_COLUMNS])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        batch_size = 2000
        qs = query.order_by('-start_time', '-created_at')

        for offset in range(0, total, batch_size):
            rows = qs[offset:offset + batch_size]
            for rec in rows:
                row = []
                for field, _ in EXPORT_COLUMNS:
                    val = getattr(rec, field, '')
                    if hasattr(val, 'strftime'):
                        val = val.strftime('%Y-%m-%d %H:%M:%S')
                    row.append(val if val is not None else '')
                writer.writerow(row)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'pgw_export_{ts}.csv'

    response = StreamingHttpResponse(stream_csv(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# =============================================================================
# Detail
# =============================================================================

@login_required
def pgw_detail(request, pk):
    """PGW CDR record detail view."""
    record = get_object_or_404(PGWRecord, pk=pk)
    return render(request, 'dashboard/pgw_detail.html', {'record': record})
