from django.conf import settings
from django.db import models


class Tariff(models.Model):
    """Versioned tariff definition for a specific operator/service/traffic combination.

    Tariffs follow an approval workflow before becoming active. Historical
    versions are never deleted — they are expired (effective_to set) when a
    new version is approved.
    """

    class ServiceType(models.TextChoices):
        VOICE = 'VOICE', 'Voice'
        SMS = 'SMS', 'SMS'
        DATA = 'DATA', 'Data'

    class TrafficType(models.TextChoices):
        ON_NET = 'ON_NET', 'On-Net'
        OFF_NET = 'OFF_NET', 'Off-Net'
        INTERNATIONAL = 'INTERNATIONAL', 'International'
        ROAMING = 'ROAMING', 'Roaming'
        INTERCONNECT = 'INTERCONNECT', 'Interconnect'
        LOCAL = 'LOCAL', 'Local'
        NATIONAL = 'NATIONAL', 'National'
        PREMIUM = 'PREMIUM', 'Premium'

    class SubscriberType(models.TextChoices):
        ALL = 'ALL', 'All'
        PREPAID = 'PREPAID', 'Prepaid'
        POSTPAID = 'POSTPAID', 'Postpaid'

    class ChargingUnit(models.TextChoices):
        PER_MINUTE = 'PER_MINUTE', 'Per Minute'
        PER_SECOND = 'PER_SECOND', 'Per Second'
        PER_MESSAGE = 'PER_MESSAGE', 'Per Message'
        PER_MB = 'PER_MB', 'Per MB'
        PER_GB = 'PER_GB', 'Per GB'
        PER_KB = 'PER_KB', 'Per KB'
        FLAT = 'FLAT', 'Flat Rate'

    class RoundingRule(models.TextChoices):
        NONE = 'NONE', 'No Rounding'
        CEIL = 'CEIL', 'Round Up'
        FLOOR = 'FLOOR', 'Round Down'
        NEAREST = 'NEAREST', 'Round to Nearest'

    class TaxTreatment(models.TextChoices):
        TAXABLE = 'TAXABLE', 'Taxable'
        EXEMPT = 'EXEMPT', 'Tax Exempt'
        ZERO_RATED = 'ZERO_RATED', 'Zero Rated'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        REVIEWED = 'REVIEWED', 'Reviewed'
        APPROVED = 'APPROVED', 'Approved'
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'

    name = models.CharField(max_length=200)
    operator_code = models.SlugField(max_length=30, db_index=True)
    service_type = models.CharField(max_length=10, choices=ServiceType.choices)
    traffic_type = models.CharField(max_length=20, choices=TrafficType.choices)
    subscriber_type = models.CharField(
        max_length=10, choices=SubscriberType.choices, default=SubscriberType.ALL,
    )
    destination = models.CharField(
        max_length=100, blank=True,
        help_text='Destination country/operator/prefix (blank = all destinations)',
    )

    rate = models.DecimalField(max_digits=12, decimal_places=2)
    charging_unit = models.CharField(max_length=12, choices=ChargingUnit.choices)
    minimum_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    rounding_rule = models.CharField(
        max_length=10, choices=RoundingRule.choices, default=RoundingRule.NONE,
    )
    currency = models.CharField(max_length=3, default='SLE')
    tax_treatment = models.CharField(
        max_length=12, choices=TaxTreatment.choices, default=TaxTreatment.TAXABLE,
    )

    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True,
    )
    is_regulatory_reference = models.BooleanField(
        default=False,
        help_text='Marks this tariff as the NatCA-approved reference rate used for compliance checks.',
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_tariffs'
        ordering = ['operator_code', 'service_type', 'traffic_type', '-version']
        indexes = [
            models.Index(fields=['operator_code', 'service_type', 'traffic_type', 'status']),
            models.Index(fields=['effective_from', 'effective_to']),
        ]

    def __str__(self):
        return f'{self.operator_code} | {self.service_type} | {self.traffic_type} v{self.version} [{self.status}]'

    @property
    def is_active_now(self):
        from datetime import date
        today = date.today()
        if self.status != self.Status.ACTIVE:
            return False
        if self.effective_from > today:
            return False
        if self.effective_to and self.effective_to < today:
            return False
        return True
