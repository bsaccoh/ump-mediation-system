"""Async wrapper for the roaming file generator."""
from __future__ import annotations

from core.tasks import tracked_task


@tracked_task('roaming.generate_file')
def task_generate_roaming_file(cycle_id: int, user_id: int | None = None):
    from django.contrib.auth import get_user_model
    from interconnect.models import BillingCycle
    from roaming.engines.generate import generate_roaming_file

    cycle = BillingCycle.objects.select_related('partner').get(pk=cycle_id)
    user = None
    if user_id:
        try:
            user = get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            user = None

    rfile = generate_roaming_file(cycle, user=user)
    return {
        'result_entity_type': 'RoamingFile',
        'result_entity_id': rfile.pk,
        'result_url': f'/roaming/files/{rfile.pk}/',
        'file_number': rfile.file_number,
        'record_count': rfile.record_count,
        'total_amount': str(rfile.total_amount),
        'currency': rfile.currency,
        'message': f'Generated {rfile.file_number} '
                    f'({rfile.record_count} records, {rfile.total_amount} {rfile.currency})',
    }
