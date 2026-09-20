from copy import copy
from datetime import date

from django.db import transaction
from django.utils import timezone

from regulatory.models import Tariff


class TariffWorkflowError(ValueError):
    """Raised when a tariff workflow action cannot be applied."""


class TariffManagementService:
    """Versioning and lifecycle rules for regulatory tariff records."""

    TRANSITIONS = {
        'submit': (Tariff.Status.DRAFT, Tariff.Status.SUBMITTED),
        'review': (Tariff.Status.SUBMITTED, Tariff.Status.REVIEWED),
        'approve': (Tariff.Status.REVIEWED, Tariff.Status.APPROVED),
        'activate': (Tariff.Status.APPROVED, Tariff.Status.ACTIVE),
    }

    @staticmethod
    def next_version(**identity):
        latest = Tariff.objects.filter(**identity).order_by('-version').values_list('version', flat=True).first()
        return (latest or 0) + 1

    @classmethod
    @transaction.atomic
    def advance(cls, tariff, action, user):
        if action == 'reject':
            allowed = {Tariff.Status.SUBMITTED, Tariff.Status.REVIEWED, Tariff.Status.APPROVED}
            if tariff.status not in allowed:
                raise TariffWorkflowError('Only submitted, reviewed, or approved tariffs can be returned to draft.')
            tariff.status = Tariff.Status.DRAFT
            tariff.save(update_fields=['status', 'updated_at'])
            return tariff

        try:
            expected_status, new_status = cls.TRANSITIONS[action]
        except KeyError as exc:
            raise TariffWorkflowError('Unknown tariff workflow action.') from exc
        if tariff.status != expected_status:
            raise TariffWorkflowError(f'Cannot {action} a tariff in {tariff.get_status_display()} status.')

        tariff.status = new_status
        now = timezone.now()
        if action == 'submit':
            tariff.submitted_by, tariff.submitted_at = user, now
        elif action == 'review':
            tariff.reviewed_by, tariff.reviewed_at = user, now
        elif action == 'approve':
            tariff.approved_by, tariff.approved_at = user, now
        elif action == 'activate':
            Tariff.objects.filter(
                operator_code=tariff.operator_code,
                service_type=tariff.service_type,
                traffic_type=tariff.traffic_type,
                subscriber_type=tariff.subscriber_type,
                destination=tariff.destination,
                status=Tariff.Status.ACTIVE,
            ).exclude(pk=tariff.pk).update(status=Tariff.Status.EXPIRED, effective_to=date.today())
        tariff.save()
        return tariff

    @staticmethod
    @transaction.atomic
    def deactivate(tariff):
        if tariff.status != Tariff.Status.ACTIVE:
            raise TariffWorkflowError('Only active tariffs can be deactivated.')
        tariff.status = Tariff.Status.EXPIRED
        tariff.effective_to = date.today()
        tariff.save(update_fields=['status', 'effective_to', 'updated_at'])
        return tariff

    @classmethod
    @transaction.atomic
    def duplicate_as_draft(cls, tariff):
        new_tariff = copy(tariff)
        new_tariff.pk = None
        new_tariff.id = None
        new_tariff.version = cls.next_version(
            operator_code=tariff.operator_code,
            service_type=tariff.service_type,
            traffic_type=tariff.traffic_type,
            subscriber_type=tariff.subscriber_type,
            destination=tariff.destination,
        )
        new_tariff.status = Tariff.Status.DRAFT
        new_tariff.effective_to = None
        new_tariff.submitted_by = new_tariff.reviewed_by = new_tariff.approved_by = None
        new_tariff.submitted_at = new_tariff.reviewed_at = new_tariff.approved_at = None
        new_tariff.save()
        return new_tariff
