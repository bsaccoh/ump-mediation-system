"""Activity log."""
from dashboard.views._common import *


# =============================================================================
# Activity Log
# =============================================================================

@login_required
def activity_log_view(request):
    return render(request, 'dashboard/activity_log.html')


@login_required
def activity_log_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    from core.models import ActivityLog

    stage = request.POST.get('stage', '').strip()
    stream = request.POST.get('stream', '').upper()
    level = request.POST.get('level', '').strip()
    search = request.POST.get('search', '').strip()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()
    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 50))

    qs = ActivityLog.objects.all()
    if stage:
        qs = qs.filter(stage=stage)
    if stream:
        qs = qs.filter(stream=stream)
    if level:
        qs = qs.filter(level=level)
    if search:
        qs = qs.filter(Q(message__icontains=search) | Q(event_type__icontains=search))
    if start_date:
        try:
            qs = qs.filter(timestamp__gte=datetime.strptime(start_date, '%Y-%m-%d'))
        except ValueError:
            pass
    if end_date:
        try:
            qs = qs.filter(timestamp__lt=datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass

    total = qs.count()

    stage_counts = dict(qs.values_list('stage').annotate(n=Count('id')))
    level_counts = dict(qs.values_list('level').annotate(n=Count('id')))

    offset = (page - 1) * per_page
    entries = qs.order_by('-timestamp')[offset:offset + per_page]
    pages = (total + per_page - 1) // per_page if total else 0

    records = []
    for e in entries:
        records.append({
            'id': e.pk,
            'timestamp': e.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'event_type': e.event_type,
            'stage': e.stage,
            'stream': e.stream or '-',
            'operator': e.operator or '-',
            'level': e.level,
            'message': e.message,
            'cdr_file_id': e.cdr_file_id,
        })

    return JsonResponse({
        'success': True,
        'records': records,
        'summary': {
            'total': total,
            'by_stage': stage_counts,
            'by_level': level_counts,
        },
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
    })


@staff_required
@require_POST
def activity_log_clear(request):
    from core.models import ActivityLog

    ids = request.POST.get('ids', '').strip()
    if ids:
        id_list = [int(x) for x in ids.split(',') if x.strip().isdigit()]
        if id_list:
            deleted, _ = ActivityLog.objects.filter(pk__in=id_list).delete()
            return JsonResponse({'success': True, 'deleted': deleted})
        return JsonResponse({'success': False, 'message': 'No valid IDs provided.'}, status=400)

    stage = request.POST.get('stage', '').strip()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()

    qs = ActivityLog.objects.all()
    if stage:
        qs = qs.filter(stage=stage)
    if start_date:
        try:
            qs = qs.filter(timestamp__gte=datetime.strptime(start_date, '%Y-%m-%d'))
        except ValueError:
            pass
    if end_date:
        try:
            qs = qs.filter(timestamp__lt=datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass

    deleted, _ = qs.delete()
    return JsonResponse({'success': True, 'deleted': deleted})


