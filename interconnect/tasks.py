"""Async wrappers for interconnect business engines.

Each ``@tracked_task``-decorated function runs in a Celery worker and
keeps a ``core.JobRecord`` row up-to-date.  Views call ``enqueue_job(...)``
which creates the row, fires the task, and returns the row PK so the UI
can poll ``/jobs/<id>/status/`` until terminal.
"""
from __future__ import annotations

from core.tasks import tracked_task


@tracked_task('interconnect.generate_invoice')
def task_generate_invoice(cycle_id: int, direction: str, user_id: int | None = None):
    """Run :func:`interconnect.engines.invoicing.generate_invoice` in a worker."""
    from django.contrib.auth import get_user_model
    from interconnect.models import BillingCycle
    from interconnect.engines.invoicing import generate_invoice

    cycle = BillingCycle.objects.select_related('partner').get(pk=cycle_id)
    user = None
    if user_id:
        try:
            user = get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            user = None

    invoice = generate_invoice(cycle, direction=direction, user=user)
    return {
        'result_entity_type': 'Invoice',
        'result_entity_id': invoice.pk,
        'result_url': f'/interconnect/invoices/{invoice.pk}/',
        'invoice_number': invoice.invoice_number,
        'total': str(invoice.total),
        'currency': invoice.currency,
        'message': f'Generated invoice {invoice.invoice_number} ({invoice.total} {invoice.currency})',
    }


@tracked_task('interconnect.apply_rates')
def task_apply_rates(cycle_id: int):
    """Run :func:`interconnect.engines.rating.apply_rates` in a worker."""
    from interconnect.models import BillingCycle
    from interconnect.engines.rating import apply_rates

    cycle = BillingCycle.objects.select_related('partner').get(pk=cycle_id)
    result = apply_rates(cycle)
    summary = result.summary()
    return {
        'result_entity_type': 'BillingCycle',
        'result_entity_id': cycle.pk,
        'result_url': f'/interconnect/cycles/',
        'event_total': summary.get('event_total', 0),
        'amount_total': summary.get('amount_total', '0'),
        'message': f'Rated {summary.get("event_total", 0)} events across {summary.get("bucket_count", 0)} buckets',
    }
