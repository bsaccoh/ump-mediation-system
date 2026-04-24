"""MSC CDR search, export, and detail views."""
import csv
import io
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404
from django.db.models import Q, Sum, Avg, Count

from .models import MSCRecord
from collection.models import DataSource


# =============================================================================
# Shared filter builder
# =============================================================================

def _build_cdr_queryset(params):
    """Build filtered MSCRecord queryset from request params dict.

    Used by both the search API and the export view.
    Returns (queryset, filter_description).
    """
    calling = params.get('calling_number', '').strip()
    called = params.get('called_number', '').strip()
    imsi = params.get('imsi', '').strip()
    imei = params.get('imei', '').strip()
    service_type = params.get('service_type', '').strip()
    record_type = params.get('record_type', '').strip()
    source_id = params.get('source_id', '').strip()
    start_date = params.get('start_date', '').strip()
    end_date = params.get('end_date', '').strip()

    query = MSCRecord.objects.select_related('file', 'source')
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
def cdr_search(request):
    """CDR search page."""
    total_records = MSCRecord.objects.count()
    sources = DataSource.objects.filter(enabled=True).order_by('name')

    return render(request, 'dashboard/cdr_search.html', {
        'total_records': total_records,
        'sources': sources,
    })


@login_required
def cdr_search_api(request):
    """CDR search API endpoint (POST)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 20))

    query, _ = _build_cdr_queryset(request.POST)
    total = query.count()

    if total == 0:
        total_in_db = MSCRecord.objects.count()
        if total_in_db > 0:
            svc_counts = (MSCRecord.objects
                          .values('service_type')
                          .annotate(count=Count('id'))
                          .order_by('-count'))
            svc_summary = ', '.join(f"{s['service_type']}: {s['count']:,}" for s in svc_counts)
            message = f'No records match. ({total_in_db:,} in DB: {svc_summary}). Try Clear then Search.'
        else:
            message = 'No records. Upload and process CDR files first.'

        return JsonResponse({
            'success': True, 'records': [],
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'stats': {'total_records': 0, 'total_duration': 0, 'avg_duration': 0},
            'message': message,
        })

    offset = (page - 1) * per_page
    records_qs = query.order_by('-created_at')[offset:offset + per_page]

    records = []
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

    stats = query.aggregate(
        total_duration=Sum('duration'),
        avg_duration=Avg('duration'),
    )
    pages = (total + per_page - 1) // per_page

    return JsonResponse({
        'success': True,
        'records': records,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
        'stats': {
            'total_records': total,
            'total_duration': stats['total_duration'] or 0,
            'avg_duration': round(stats['avg_duration'] or 0, 2),
        },
    })


# =============================================================================
# Export
# =============================================================================

EXPORT_COLUMNS = [
    ('record_type', 'Record Type'),
    ('service_type', 'Service Type'),
    ('call_direction', 'Direction'),
    ('calling_number', 'Calling Number'),
    ('called_number', 'Called Number'),
    ('dialed_number', 'Dialed Number'),
    ('charged_msisdn', 'Charged MSISDN'),
    ('imsi', 'IMSI'),
    ('imei', 'IMEI'),
    ('start_time', 'Start Time'),
    ('end_time', 'End Time'),
    ('duration', 'Duration (s)'),
    ('msc_id', 'MSC ID'),
    ('cell_id', 'Cell ID'),
    ('lac', 'LAC'),
    ('rat_type', 'RAT Type'),
    ('originating_trunk', 'Originating Trunk'),
    ('terminating_trunk', 'Terminating Trunk'),
    ('teleservice_code', 'Teleservice Code'),
    ('result_code', 'Result Code'),
    ('roaming_indicator', 'Roaming'),
    ('network_record_id', 'Network Record ID'),
    ('call_reference', 'Call Reference'),
    ('status', 'Status'),
]

MAX_EXPORT_ROWS = 100000


@login_required
def cdr_export(request):
    """Export filtered CDR records as CSV.

    Accepts same filter params as cdr_search_api via POST.
    Streams CSV to avoid memory issues on large exports.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    query, filter_desc = _build_cdr_queryset(request.POST)
    total = query.count()

    if total > MAX_EXPORT_ROWS:
        return JsonResponse({
            'error': f'Too many records ({total:,}). Maximum export is {MAX_EXPORT_ROWS:,}. Add more filters.'
        }, status=400)

    if total == 0:
        return JsonResponse({'error': 'No records match the filters.'}, status=404)

    def stream_csv():
        """Generator that yields CSV rows."""
        buf = io.StringIO()
        writer = csv.writer(buf)

        # Header
        writer.writerow([col[1] for col in EXPORT_COLUMNS])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        # Data rows in batches
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
    filename = f'cdr_export_{ts}.csv'

    response = StreamingHttpResponse(stream_csv(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# =============================================================================
# Detail
# =============================================================================

@login_required
def cdr_detail(request, pk):
    """CDR record detail view."""
    record = get_object_or_404(MSCRecord, pk=pk)
    return render(request, 'dashboard/cdr_detail.html', {'record': record})


# =============================================================================
# Per-Subscriber View
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

    # Match as caller, called, or charged MSISDN
    query = MSCRecord.objects.filter(
        Q(calling_number__icontains=msisdn) |
        Q(called_number__icontains=msisdn) |
        Q(charged_msisdn__icontains=msisdn)
    ).select_related('file', 'source')

    total = query.count()
    if total == 0:
        return JsonResponse({
            'success': True, 'records': [], 'summary': {},
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'message': f'No activity found for "{msisdn}". Try a partial number or different format.',
        })

    # Summary stats
    summary_data = query.aggregate(
        total_voice=Count('id', filter=Q(service_type='VOICE')),
        total_sms=Count('id', filter=Q(service_type='SMS')),
        total_other=Count('id', filter=~Q(service_type__in=['VOICE', 'SMS'])),
        total_duration=Sum('duration'),
        avg_duration=Avg('duration', filter=Q(service_type='VOICE')),
    )

    # Date range
    from django.db.models import Min, Max
    date_range = query.aggregate(first=Min('start_time'), last=Max('start_time'))

    # IMSI / IMEI (most common)
    imsi_qs = (query.exclude(imsi='').values('imsi')
               .annotate(cnt=Count('id')).order_by('-cnt')[:1])
    imei_qs = (query.exclude(imei='').values('imei')
               .annotate(cnt=Count('id')).order_by('-cnt')[:1])

    summary = {
        'msisdn': msisdn,
        'total_records': total,
        'total_voice': summary_data['total_voice'] or 0,
        'total_sms': summary_data['total_sms'] or 0,
        'total_other': summary_data['total_other'] or 0,
        'total_duration': summary_data['total_duration'] or 0,
        'avg_duration': round(summary_data['avg_duration'] or 0, 1),
        'first_activity': date_range['first'].strftime('%Y-%m-%d %H:%M') if date_range['first'] else '-',
        'last_activity': date_range['last'].strftime('%Y-%m-%d %H:%M') if date_range['last'] else '-',
        'primary_imsi': imsi_qs[0]['imsi'] if imsi_qs else '-',
        'primary_imei': imei_qs[0]['imei'] if imei_qs else '-',
    }

    # Paginated records (chronological)
    offset = (page - 1) * per_page
    records_qs = query.order_by('-start_time', '-created_at')[offset:offset + per_page]

    records = []
    for rec in records_qs:
        # Determine role: was this MSISDN the caller or the called party?
        role = 'CALLER'
        if msisdn in (rec.called_number or ''):
            role = 'CALLED'
        elif msisdn in (rec.charged_msisdn or '') and msisdn not in (rec.calling_number or ''):
            role = 'CHARGED'

        other_party = rec.called_number if role == 'CALLER' else rec.calling_number

        records.append({
            'id': rec.pk,
            'record_type': rec.record_type,
            'service_type': rec.service_type,
            'role': role,
            'other_party': other_party or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.called_number,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'duration': rec.duration,
            'msc_id': rec.msc_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'status': rec.status,
        })

    pages = (total + per_page - 1) // per_page

    return JsonResponse({
        'success': True,
        'records': records,
        'summary': summary,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
    })
