"""Cross-platform Job views.

Browse + poll the status of long-running tasks (invoice generation, NATCOM
reports, LEA exports, roaming-file generation, etc.) that go through
``core.JobRecord``.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404

from .models import JobRecord


def _paginate(qs, page, per_page=25):
    total = qs.count()
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), pages))
    offset = (page - 1) * per_page
    return qs[offset:offset + per_page], total, page, pages


@login_required
def job_list(request):
    return render(request, 'core/jobs.html', {
        'title': 'Background Jobs',
        'total': JobRecord.objects.count(),
        'status_choices': JobRecord.Status.choices,
    })


@login_required
def job_api(request):
    """JSON list of jobs with optional ``?status=`` + ``?q=`` filters."""
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    page = request.GET.get('page', 1)
    qs = JobRecord.objects.select_related('submitted_by').all()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(job_type__icontains=q) | Q(label__icontains=q))
    rows, total, page, pages = _paginate(qs, page)
    data = [_serialize(r) for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
def job_status(request, pk):
    """Single-job poll endpoint — UI polls this until status is terminal."""
    job = get_object_or_404(JobRecord, pk=pk)
    return JsonResponse(_serialize(job))


@login_required
def job_detail(request, pk):
    job = get_object_or_404(JobRecord, pk=pk)
    return render(request, 'core/job_detail.html', {
        'title': f'Job #{job.pk}',
        'job': job,
    })


def _serialize(job: JobRecord) -> dict:
    return {
        'id': job.pk,
        'job_type': job.job_type,
        'label': job.label,
        'status': job.status,
        'status_display': job.get_status_display(),
        'is_terminal': job.is_terminal,
        'progress_pct': job.progress_pct,
        'progress_message': job.progress_message,
        'submitted_at': job.submitted_at.isoformat() if job.submitted_at else '',
        'started_at': job.started_at.isoformat() if job.started_at else '',
        'finished_at': job.finished_at.isoformat() if job.finished_at else '',
        'duration_seconds': job.duration_seconds,
        'submitted_by': job.submitted_by.username if job.submitted_by else '',
        'celery_task_id': job.celery_task_id,
        'params': job.params or {},
        'result': job.result or {},
        'error_message': job.error_message,
        'result_entity_type': job.result_entity_type,
        'result_entity_id': job.result_entity_id,
        'result_url': job.result_url,
    }
