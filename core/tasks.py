"""Celery tasks + tracking infrastructure for the platform.

This module exports two things on top of the legacy ``dispatch_cdr_file_task``:

* :func:`tracked_task` — a decorator that wraps a Celery shared_task so it
  automatically updates a ``core.JobRecord`` row with PENDING → RUNNING →
  SUCCESS/FAILURE transitions, captures the result + error, and (when the
  task returns a dict containing ``result_entity_type``/``result_entity_id``)
  links back to the produced entity.

* :func:`enqueue_job` — helper used by views to create the ``JobRecord``
  row + enqueue the matching Celery task in one atomic step.  Returns the
  ``JobRecord`` so the view can redirect to its status page.

Falls back to in-process execution if Celery is not installed so unit
tests keep working under any settings.
"""
from __future__ import annotations

import logging
import traceback
from functools import wraps
from typing import Any, Callable

from django.utils import timezone

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:  # pragma: no cover
    shared_task = None


# ---------------------------------------------------------------------------
# Existing dispatch task (untouched API)
# ---------------------------------------------------------------------------

def _run_dispatch(cdr_file_id: int):
    from core.dispatcher import dispatch_cdr_file
    return dispatch_cdr_file(cdr_file_id)


if shared_task is not None:
    @shared_task(name='core.dispatch_cdr_file_task')
    def dispatch_cdr_file_task(cdr_file_id: int):
        return _run_dispatch(cdr_file_id)
else:
    class _SyncTask:
        @staticmethod
        def delay(cdr_file_id: int):
            return _run_dispatch(cdr_file_id)

        def __call__(self, cdr_file_id: int):
            return _run_dispatch(cdr_file_id)

    dispatch_cdr_file_task = _SyncTask()


# ---------------------------------------------------------------------------
# JobRecord tracking — tracked_task decorator + enqueue_job helper
# ---------------------------------------------------------------------------

def tracked_task(task_name: str):
    """Decorator: wraps a function so Celery runs it and JobRecord updates.

    Usage::

        @tracked_task('interconnect.generate_invoice')
        def _do_generate_invoice(cycle_id, direction, user_id):
            from interconnect.engines.invoicing import generate_invoice
            ...
            return {
                'result_entity_type': 'Invoice',
                'result_entity_id': str(inv.pk),
                'result_url': f'/interconnect/invoices/{inv.pk}/',
                'message': f'Generated {inv.invoice_number}',
            }

    The first positional argument to the wrapped task is the ``job_id``
    (the ``JobRecord.pk`` that was created at enqueue time).
    """
    def decorator(fn: Callable[..., Any]):
        @wraps(fn)
        def runner(job_id: int, *args, **kwargs):
            from core.models import JobRecord
            try:
                job = JobRecord.objects.get(pk=job_id)
            except JobRecord.DoesNotExist:
                logger.warning(f'tracked_task: JobRecord #{job_id} missing — running anyway')
                return fn(*args, **kwargs)

            job.status = JobRecord.Status.RUNNING
            job.started_at = timezone.now()
            job.save(update_fields=['status', 'started_at'])

            try:
                result = fn(*args, **kwargs) or {}
                update = {
                    'status': JobRecord.Status.SUCCESS,
                    'finished_at': timezone.now(),
                    'progress_pct': 100,
                }
                if isinstance(result, dict):
                    if 'result_entity_type' in result:
                        update['result_entity_type'] = result['result_entity_type']
                    if 'result_entity_id' in result:
                        update['result_entity_id'] = str(result['result_entity_id'])
                    if 'result_url' in result:
                        update['result_url'] = result['result_url']
                    job.result = {
                        k: v for k, v in result.items()
                        if k not in {'result_entity_type', 'result_entity_id', 'result_url'}
                    }
                for k, v in update.items():
                    setattr(job, k, v)
                job.save()
                return result
            except Exception as exc:  # pragma: no cover - exception path
                logger.exception(f'tracked_task {task_name} failed for job #{job_id}')
                job.status = JobRecord.Status.FAILURE
                job.finished_at = timezone.now()
                job.error_message = f'{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[:4000]}'
                job.save(update_fields=['status', 'finished_at', 'error_message'])
                raise

        if shared_task is not None:
            return shared_task(name=task_name)(runner)

        class _SyncWrapper:
            @staticmethod
            def delay(job_id: int, *args, **kwargs):
                return runner(job_id, *args, **kwargs)

            @staticmethod
            def apply_async(args=None, kwargs=None, **_kw):
                args = list(args or [])
                kwargs = dict(kwargs or {})
                return runner(*args, **kwargs)

            def __call__(self, job_id: int, *args, **kwargs):
                return runner(job_id, *args, **kwargs)

        return _SyncWrapper()

    return decorator


def enqueue_job(*, task, job_type: str, label: str, user=None,
                params: dict | None = None,
                args: tuple = (), kwargs: dict | None = None):
    """Create a ``JobRecord`` then enqueue the matching Celery task.

    Returns the new ``JobRecord``.  The view should redirect / respond with
    the job's status URL so the UI can poll until completion.
    """
    from core.models import JobRecord
    job = JobRecord.objects.create(
        job_type=job_type,
        label=label[:200],
        submitted_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
        params=params or {},
        status=JobRecord.Status.PENDING,
    )
    try:
        async_result = task.delay(job.pk, *args, **(kwargs or {}))
        if hasattr(async_result, 'id'):
            job.celery_task_id = async_result.id
            job.save(update_fields=['celery_task_id'])
    except Exception as exc:
        job.status = JobRecord.Status.FAILURE
        job.error_message = f'Failed to enqueue: {exc}'
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'error_message', 'finished_at'])
        raise
    return job
