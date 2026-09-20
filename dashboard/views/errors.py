"""File error management — lists CDRFiles that failed processing."""
from dashboard.views._common import *


@login_required
def errors_view(request):
    return render(request, 'dashboard/errors.html')


@login_required
def errors_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    stream = request.POST.get('stream', '').upper()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()
    status_filter = request.POST.get('status', '').strip()
    search = request.POST.get('search', '').strip()
    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 50))

    error_statuses = ['FAILED', 'EMPTY']
    if status_filter and status_filter in error_statuses:
        qs = CDRFile.objects.filter(status=status_filter)
    else:
        qs = CDRFile.objects.filter(status__in=error_statuses)

    if stream:
        qs = qs.filter(decoder_type=stream)
    if start_date:
        try:
            qs = qs.filter(created_at__gte=datetime.strptime(start_date, '%Y-%m-%d'))
        except ValueError:
            pass
    if end_date:
        try:
            qs = qs.filter(created_at__lt=datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass
    if search:
        qs = qs.filter(Q(filename__icontains=search) | Q(error_message__icontains=search))

    total = qs.count()
    total_size = qs.aggregate(s=Sum('file_size'))['s'] or 0

    by_stream = list(
        qs.values('decoder_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    by_status = list(
        qs.values('status')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    offset = (page - 1) * per_page
    files = qs.select_related('source').order_by('-created_at')[offset:offset + per_page]
    pages = (total + per_page - 1) // per_page if total else 0

    records = []
    for f in files:
        records.append({
            'id': f.pk,
            'filename': f.filename,
            'stream': f.decoder_type or '-',
            'operator': f.operator_code or '-',
            'file_size': f.file_size,
            'status': f.status,
            'error_message': f.error_message or '',
            'source': f.source.name if f.source else '-',
            'created_at': f.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'processing_started': f.processing_started.strftime('%Y-%m-%d %H:%M:%S') if f.processing_started else None,
        })

    return JsonResponse({
        'success': True,
        'records': records,
        'summary': {
            'total': total,
            'total_size': total_size,
            'by_stream': [{'stream': r['decoder_type'] or '-', 'count': r['count']} for r in by_stream],
            'by_status': [{'status': r['status'], 'count': r['count']} for r in by_status],
        },
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
    })
