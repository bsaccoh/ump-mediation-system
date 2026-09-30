"""Run a tracked task, honouring the USE_CELERY setting.

The platform's ``core.tasks.enqueue_job`` always calls ``task.delay()``, which
reaches for the Celery broker (Redis). When no worker/broker is deployed —
the default for this module and for local development (``USE_CELERY=False``) —
that connection fails, and not every failure mode is caught by enqueue_job's
broker-error fallback (Celery can surface it as a plain ``RuntimeError``).

So when Celery is disabled we skip the broker entirely and run the task in the
current process. Calling a Celery task object directly (``task(job_id, ...)``)
executes its body synchronously without contacting the broker or result
backend — exactly the JobRecord-tracked, no-worker behaviour we want in dev.
When ``USE_CELERY`` is true we defer to the platform's enqueue_job as normal.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger('drive_test')


def run_tracked(*, task, job_type: str, label: str, user=None,
                params: dict | None = None, args: tuple = ()):
    """Create a JobRecord and run ``task`` — via Celery if enabled, else in
    process. Returns the JobRecord.
    """
    from core.models import JobRecord

    if getattr(settings, 'USE_CELERY', False):
        from core.tasks import enqueue_job
        return enqueue_job(task=task, job_type=job_type, label=label,
                           user=user, params=params, args=args)

    job = JobRecord.objects.create(
        job_type=job_type,
        label=label[:200],
        submitted_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
        params=params or {},
        status=JobRecord.Status.PENDING,
    )
    try:
        # Direct call runs the tracked runner synchronously — no broker.
        task(job.pk, *args)
    except Exception as exc:  # pragma: no cover - defensive; task marks its own state
        logger.exception('Synchronous job %s (%s) failed', job.pk, job_type)
        job.refresh_from_db()
        if job.status not in (JobRecord.Status.FAILURE, JobRecord.Status.SUCCESS):
            from django.utils import timezone
            job.status = JobRecord.Status.FAILURE
            job.error_message = f'{type(exc).__name__}: {exc}'
            job.finished_at = timezone.now()
            job.save(update_fields=['status', 'error_message', 'finished_at'])
    return job
