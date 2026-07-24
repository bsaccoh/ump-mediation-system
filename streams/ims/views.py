"""
IMS Stream Views
=================
Search, list and per-record detail views for IMS CDR records.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count, Sum, Avg
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from streams.ims.models import IMSRecord
from collection.models import DataSource


# =============================================================================
# Search list page
# =============================================================================

@login_required
def cdr_search(request):
    """IMS CDR search page (HTML)."""
    total_records = IMSRecord.objects.count()
    sources = DataSource.objects.filter(enabled=True).order_by('name')
    # Pre-fill from query string so "Show orphans" links from File Detail work.
    return render(request, 'dashboard/ims_search.html', {
        'total_records': total_records,
        'sources': sources,
        'prefill_paired':  request.GET.get('paired', ''),
        'prefill_file_id': request.GET.get('file_id', ''),
    })


@login_required
def cdr_search_api(request):
    """IMS CDR search API endpoint (POST)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 20))

    qs = IMSRecord.objects.all()

    # Apply filters
    calling   = request.POST.get('calling_number', '').strip()
    called    = request.POST.get('called_number', '').strip()
    imsi      = request.POST.get('imsi', '').strip()
    sip_method = request.POST.get('sip_method', '').strip()
    service_type = request.POST.get('service_type', '').strip()
    record_type  = request.POST.get('record_type', '').strip()
    start_from = request.POST.get('start_from', '').strip()
    start_to   = request.POST.get('start_to', '').strip()
    session_id = request.POST.get('session_id', '').strip()

    if calling:
        qs = qs.filter(calling_number__icontains=calling)
    if called:
        qs = qs.filter(called_number__icontains=called)
    if imsi:
        qs = qs.filter(imsi__icontains=imsi)
    if sip_method:
        qs = qs.filter(sip_method__iexact=sip_method)
    if service_type:
        qs = qs.filter(service_type__iexact=service_type)
    if record_type:
        qs = qs.filter(record_type__iexact=record_type)
    if session_id:
        qs = qs.filter(session_id__icontains=session_id)
    if start_from:
        qs = qs.filter(start_time__gte=start_from)
    if start_to:
        qs = qs.filter(start_time__lte=start_to)

    # Pair-correlation filter — used by the "Show orphans" badge link on
    # File Detail and by the unified search.
    paired = (request.POST.get('paired') or '').strip().lower()
    if paired == 'yes':
        qs = qs.filter(paired_record__isnull=False)
    elif paired == 'no':
        qs = qs.filter(paired_record__isnull=True)

    # Scope to one file when the badge link supplies file_id
    file_id = (request.POST.get('file_id') or '').strip()
    if file_id.isdigit():
        qs = qs.filter(file_id=int(file_id))

    total = qs.count()
    if total == 0:
        in_db = IMSRecord.objects.count()
        if in_db == 0:
            message = 'No IMS records. Upload an ATS9900 IMS CDR file first.'
        else:
            message = f'No records match. ({in_db:,} IMS records in DB.) Clear filters and try again.'
        return JsonResponse({
            'success': True, 'records': [],
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'stats': {'total_records': 0, 'total_duration': 0, 'avg_duration': 0},
            'message': message,
        })

    offset = (page - 1) * per_page
    recs = qs.order_by('-start_time', '-created_at')[offset:offset + per_page]

    records = [{
        'id': r.pk,
        'record_type':   r.record_type,
        'service_type':  r.service_type,
        'sip_method':    r.sip_method,
        'role_of_node':  r.role_of_node,
        'calling_number': r.calling_number,
        'called_number':  r.called_number,
        'imsi':           r.imsi,
        'msisdn':         r.msisdn,
        'session_id':     r.session_id,
        'icid':           r.icid,
        'node_address':   r.node_address,
        'originating_ioi': r.originating_ioi,
        'terminating_ioi': r.terminating_ioi,
        'call_property':   r.call_property,
        'call_category':   r.call_category,
        'roaming_indicator': r.roaming_indicator,
        'charging_category': r.charging_category,
        'start_time': r.start_time.strftime('%Y-%m-%d %H:%M:%S') if r.start_time else '',
        'end_time':   r.end_time.strftime('%Y-%m-%d %H:%M:%S')   if r.end_time   else '',
        'duration':   r.duration,
        'cause_for_closing': r.cause_for_closing,
        'status': r.status,
    } for r in recs]

    stats = qs.aggregate(
        total_duration=Sum('duration'),
        avg_duration=Avg('duration'),
        voice_count=Count('id', filter=Q(service_type='VOICE')),
        sms_count=Count('id', filter=Q(service_type='SMS')),
        event_count=Count('id', filter=Q(service_type='EVENT')),
    )
    pages = (total + per_page - 1) // per_page

    return JsonResponse({
        'success': True,
        'records': records,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
        'stats': {
            'total_records':  total,
            'total_duration': stats['total_duration'] or 0,
            'avg_duration':   round(stats['avg_duration'] or 0, 2),
            'voice_count':    stats['voice_count'] or 0,
            'sms_count':      stats['sms_count'] or 0,
            'event_count':    stats['event_count'] or 0,
        },
    })


# =============================================================================
# Per-record detail
# =============================================================================

@login_required
def cdr_detail(request, pk):
    """IMS record detail page."""
    record = get_object_or_404(IMSRecord, pk=pk)
    return render(request, 'dashboard/ims_detail.html', {'record': record})
