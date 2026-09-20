"""Cross-platform Job + User-management views."""
from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_http_methods

from .models import JobRecord, User


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


def _is_superuser(u):
    return u.is_superuser


# ── User Management ──────────────────────────────────────────────────────────

@login_required
@user_passes_test(_is_superuser)
def user_list(request):
    users = User.objects.all().order_by('-is_active', '-is_superuser', 'username')
    stats = {
        'total': users.count(),
        'active': users.filter(is_active=True).count(),
        'inactive': users.filter(is_active=False).count(),
        'superusers': users.filter(is_superuser=True).count(),
        'staff': users.filter(is_staff=True).count(),
    }
    return render(request, 'core/users.html', {'users': users, 'stats': stats})


@login_required
@user_passes_test(_is_superuser)
@require_http_methods(['GET', 'POST'])
def users_api(request):
    if request.method == 'GET':
        uid = request.GET.get('id')
        if uid:
            u = get_object_or_404(User, pk=uid)
            return JsonResponse({'user': _serialize_user(u)})
        return JsonResponse({'error': 'id required'}, status=400)

    data = json.loads(request.body)
    action = data.get('action')

    if action == 'toggle':
        u = get_object_or_404(User, pk=data['user_id'])
        if u.pk == request.user.pk:
            return JsonResponse({'success': False, 'error': 'Cannot deactivate yourself.'})
        u.is_active = not u.is_active
        u.save(update_fields=['is_active'])
        return JsonResponse({'success': True})

    if action == 'create':
        return _create_user(data)

    if action == 'edit':
        return _edit_user(data, request.user)

    return JsonResponse({'error': 'Unknown action'}, status=400)


def _create_user(data):
    username = (data.get('username') or '').strip()
    password = data.get('password', '')
    password2 = data.get('password_confirm', '')

    if not username:
        return JsonResponse({'success': False, 'error': 'Username is required.'})
    if User.objects.filter(username=username).exists():
        return JsonResponse({'success': False, 'error': f'Username "{username}" already exists.'})
    if not password or len(password) < 8:
        return JsonResponse({'success': False, 'error': 'Password must be at least 8 characters.'})
    if password != password2:
        return JsonResponse({'success': False, 'error': 'Passwords do not match.'})

    u = User(
        username=username,
        email=data.get('email', ''),
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', ''),
        phone=data.get('phone', ''),
        department=data.get('department', ''),
        is_active=bool(data.get('is_active', True)),
        is_staff=bool(data.get('is_staff', False)),
        is_superuser=bool(data.get('is_superuser', False)),
        is_operator=bool(data.get('is_operator', False)),
        is_analyst=bool(data.get('is_analyst', False)),
        can_lawful_intercept=bool(data.get('can_lawful_intercept', False)),
    )
    u.set_password(password)
    u.save()
    return JsonResponse({'success': True, 'id': u.pk})


def _edit_user(data, current_user):
    u = get_object_or_404(User, pk=data.get('user_id'))

    u.email = data.get('email', u.email)
    u.first_name = data.get('first_name', u.first_name)
    u.last_name = data.get('last_name', u.last_name)
    u.phone = data.get('phone', u.phone)
    u.department = data.get('department', u.department)
    u.is_staff = bool(data.get('is_staff', u.is_staff))
    u.is_operator = bool(data.get('is_operator', u.is_operator))
    u.is_analyst = bool(data.get('is_analyst', u.is_analyst))
    u.can_lawful_intercept = bool(data.get('can_lawful_intercept', u.can_lawful_intercept))

    if u.pk != current_user.pk:
        u.is_active = bool(data.get('is_active', u.is_active))
        u.is_superuser = bool(data.get('is_superuser', u.is_superuser))

    password = data.get('password', '')
    if password:
        password2 = data.get('password_confirm', '')
        if len(password) < 8:
            return JsonResponse({'success': False, 'error': 'Password must be at least 8 characters.'})
        if password != password2:
            return JsonResponse({'success': False, 'error': 'Passwords do not match.'})
        u.set_password(password)

    u.save()
    return JsonResponse({'success': True})


def _serialize_user(u):
    return {
        'id': u.pk,
        'username': u.username,
        'email': u.email,
        'first_name': u.first_name,
        'last_name': u.last_name,
        'phone': u.phone,
        'department': u.department,
        'is_active': u.is_active,
        'is_staff': u.is_staff,
        'is_superuser': u.is_superuser,
        'is_operator': u.is_operator,
        'is_analyst': u.is_analyst,
        'can_lawful_intercept': u.can_lawful_intercept,
        'last_login': u.last_login.isoformat() if u.last_login else None,
    }


# ── Jobs ─────────────────────────────────────────────────────────────────────

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
