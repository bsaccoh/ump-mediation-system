"""Duplicate management."""
from dashboard.views._common import *


# =============================================================================
# Duplicate Management
# =============================================================================

@login_required
def duplicates_view(request):
    return render(request, 'dashboard/duplicates.html')


@login_required
def duplicates_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    stream = request.POST.get('stream', '').upper()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()
    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 50))

    qs = CDRFile.objects.filter(status=CDRFile.Status.DUPLICATE)
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

    total = qs.count()
    total_size = qs.aggregate(s=Sum('file_size'))['s'] or 0

    by_stream = list(
        qs.values('decoder_type')
        .annotate(count=Count('id'), size=Sum('file_size'))
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
            'file_hash': f.file_hash,
            'source': f.source.name if f.source else '-',
            'created_at': f.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        })

    return JsonResponse({
        'success': True,
        'records': records,
        'summary': {
            'total': total,
            'total_size': total_size,
            'by_stream': [{'stream': r['decoder_type'] or '-', 'count': r['count'],
                           'size': r['size'] or 0} for r in by_stream],
        },
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
    })


@operator_required
@require_POST
def duplicates_delete(request):
    import os as _os

    stream = request.POST.get('stream', '').upper()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()
    file_ids = request.POST.getlist('ids')

    qs = CDRFile.objects.filter(status=CDRFile.Status.DUPLICATE)
    if file_ids:
        qs = qs.filter(pk__in=file_ids)
    else:
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

    deleted_count = 0
    freed_bytes = 0
    for f in qs.iterator():
        if f.file_path and _os.path.isfile(f.file_path):
            try:
                freed_bytes += _os.path.getsize(f.file_path)
                _os.remove(f.file_path)
            except OSError:
                pass
        deleted_count += 1
    qs.delete()

    return JsonResponse({
        'success': True,
        'deleted': deleted_count,
        'freed_bytes': freed_bytes,
    })


