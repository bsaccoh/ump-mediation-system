from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re

from django.db import transaction
from django.db.models import Q

from core.models import AuditLog
from regulatory.models import TaxRate, TaxType


class TaxRateValidationError(ValueError):
    """A form-safe validation error for tax configuration operations."""

    def __init__(self, field_errors):
        super().__init__('Invalid tax type configuration.')
        self.field_errors = field_errors


class TaxRateService:
    """Tax master-data, versioning, and effective-rate selection rules."""

    @staticmethod
    def display_status(rate, on_date=None):
        on_date = on_date or date.today()
        if not rate.tax_type.is_active or rate.status == TaxRate.Status.INACTIVE:
            return TaxRate.Status.INACTIVE
        if rate.status == TaxRate.Status.DRAFT:
            return TaxRate.Status.DRAFT
        if rate.effective_from > on_date:
            return TaxRate.Status.SCHEDULED
        if rate.effective_to and rate.effective_to < on_date:
            return TaxRate.Status.EXPIRED
        return TaxRate.Status.ACTIVE

    @classmethod
    def get_rate_rows(cls, *, category='', status='', date_from='', date_to='', search='', ordering='name'):
        rates = TaxRate.objects.select_related('tax_type', 'created_by').all()
        if category:
            rates = rates.filter(tax_type__category=category)
        if date_from:
            rates = rates.filter(effective_from__gte=date_from)
        if date_to:
            rates = rates.filter(Q(effective_to__isnull=True) | Q(effective_to__lte=date_to))
        if search:
            rates = rates.filter(
                Q(tax_type__name__icontains=search) | Q(tax_type__code__icontains=search) |
                Q(tax_type__category__icontains=search) | Q(tax_type__description__icontains=search)
            )
        orderings = {'name': ('tax_type__name', '-version'), '-name': ('-tax_type__name', '-version'),
                     'effective': ('-effective_from',), '-effective': ('effective_from',),
                     'category': ('tax_type__category', 'tax_type__name')}
        rates = rates.order_by(*orderings.get(ordering, orderings['name']))
        rows = [{'rate': rate, 'display_status': cls.display_status(rate)} for rate in rates]
        return [row for row in rows if not status or row['display_status'] == status]

    @classmethod
    def summary(cls):
        rates = list(TaxRate.objects.select_related('tax_type').all())
        statuses = [cls.display_status(rate) for rate in rates]
        last_updated = TaxRate.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()
        return {
            'total_tax_types': TaxType.objects.count(),
            'active_rates': statuses.count(TaxRate.Status.ACTIVE),
            'expired_rates': statuses.count(TaxRate.Status.EXPIRED),
            'last_updated': last_updated,
        }

    @staticmethod
    def _invalidate_calculator_cache():
        from regulatory.services import tax_calculator
        tax_calculator._TAX_CACHE_LOADED = False
        tax_calculator._TAX_RATE_CACHE = []
        tax_calculator._CACHE_TIMESTAMP = None

    @classmethod
    @transaction.atomic
    def create_tax_type_with_initial_rate(cls, *, name, code, category, description, rate_percent,
                                          effective_from, effective_to=None, status=TaxRate.Status.DRAFT,
                                          user=None):
        normalized_code = (code or '').strip().upper()
        errors = {}
        if len((name or '').strip()) < 3:
            errors['name'] = 'Name must contain at least 3 characters.'
        if not normalized_code:
            errors['code'] = 'Code is required.'
        elif not re.fullmatch(r'[A-Z0-9_-]+', normalized_code):
            errors['code'] = 'Use only letters, numbers, dashes, and underscores.'
        elif TaxType.objects.filter(code__iexact=normalized_code).exists():
            errors['code'] = f'A tax type with code “{normalized_code}” already exists.'
        valid_categories = {choice for choice, _ in TaxType.Category.choices}
        if category not in valid_categories:
            errors['category'] = 'Select a valid tax category.'
        try:
            initial_rate = Decimal(str(rate_percent))
            if initial_rate < 0:
                errors['rate_percent'] = 'Initial rate must be greater than or equal to 0.'
        except (InvalidOperation, TypeError):
            errors['rate_percent'] = 'Initial rate must be a valid number.'
            initial_rate = None
        if not effective_from:
            errors['effective_from'] = 'Effective from date is required when creating an initial rate.'
        elif effective_to and effective_to < effective_from:
            errors['effective_to'] = 'Effective to date cannot be earlier than effective from date.'
        valid_statuses = {TaxRate.Status.DRAFT, TaxRate.Status.SCHEDULED, TaxRate.Status.ACTIVE}
        if status not in valid_statuses:
            errors['status'] = 'Select Draft, Scheduled, or Active.'
        if errors:
            raise TaxRateValidationError(errors)

        today = date.today()
        if effective_to and effective_to < today:
            status = TaxRate.Status.EXPIRED
        elif status == TaxRate.Status.ACTIVE and effective_from > today:
            status = TaxRate.Status.SCHEDULED
        elif status == TaxRate.Status.SCHEDULED and effective_from <= today:
            status = TaxRate.Status.ACTIVE

        tax_type = TaxType(name=name.strip(), code=normalized_code, category=category, description=(description or '').strip())
        try:
            tax_type.full_clean()
        except Exception as exc:
            error_messages = getattr(exc, 'message_dict', {'name': 'Unable to validate tax type.'})
            raise TaxRateValidationError({key: values[0] for key, values in error_messages.items()}) from exc
        tax_type.save()
        AuditLog.objects.create(user=user, action='CREATE', entity_type='TaxType', entity_id=str(tax_type.pk),
                                description=f'Created tax type {tax_type.code}.',
                                extra_data={'code': tax_type.code, 'category': tax_type.category})
        rate = cls.add_rate_version(
            tax_type=tax_type, rate_percent=initial_rate, effective_from=effective_from,
            effective_to=effective_to, status=status, user=user,
            notes='Initial rate version created with tax type.',
        )
        return tax_type, rate

    @classmethod
    @transaction.atomic
    def add_rate_version(cls, *, tax_type, rate_percent, effective_from, effective_to=None, notes='', status=TaxRate.Status.ACTIVE, user=None):
        version = (TaxRate.objects.filter(tax_type=tax_type).order_by('-version').values_list('version', flat=True).first() or 0) + 1
        new_rate = TaxRate.objects.create(
            tax_type=tax_type, rate_percent=rate_percent, effective_from=effective_from,
            effective_to=effective_to, notes=notes, status=status, version=version, created_by=user,
        )
        if status in {TaxRate.Status.ACTIVE, TaxRate.Status.SCHEDULED}:
            TaxRate.objects.filter(
                tax_type=tax_type,
                status__in=[TaxRate.Status.ACTIVE, TaxRate.Status.SCHEDULED],
                effective_from__lt=effective_from,
            ).exclude(pk=new_rate.pk).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=effective_from)
            ).update(effective_to=effective_from - timedelta(days=1))
        AuditLog.objects.create(user=user, action='CREATE', entity_type='TaxRate', entity_id=str(new_rate.pk),
                                description=f'Created {tax_type.code} rate version v{version}.')
        cls._invalidate_calculator_cache()
        return new_rate

    @classmethod
    @transaction.atomic
    def update_tax_type(cls, tax_type, *, name, category, description, user=None):
        tax_type.name, tax_type.category, tax_type.description = name, category, description
        tax_type.full_clean()
        tax_type.save(update_fields=['name', 'category', 'description', 'updated_at'])
        AuditLog.objects.create(user=user, action='UPDATE', entity_type='TaxType', entity_id=str(tax_type.pk),
                                description=f'Updated tax type {tax_type.code}.')
        return tax_type

    @classmethod
    @transaction.atomic
    def deactivate_tax_type(cls, tax_type, user=None):
        tax_type.is_active = False
        tax_type.save(update_fields=['is_active', 'updated_at'])
        AuditLog.objects.create(user=user, action='UPDATE', entity_type='TaxType', entity_id=str(tax_type.pk),
                                description=f'Deactivated tax type {tax_type.code}.')
        cls._invalidate_calculator_cache()

    @staticmethod
    def find_effective_rate(tax_type, event_date):
        return TaxRate.objects.filter(
            tax_type=tax_type,
            status__in=[TaxRate.Status.ACTIVE, TaxRate.Status.SCHEDULED],
            effective_from__lte=event_date,
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=event_date)
        ).order_by('-effective_from', '-version').first()
