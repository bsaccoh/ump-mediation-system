from django.conf import settings
from django.db import models


class TaxType(models.Model):
    """A category of tax or regulatory levy (e.g. GST, Communications Service Tax)."""

    class ServiceType(models.TextChoices):
        VOICE = 'VOICE', 'Voice'
        SMS = 'SMS', 'SMS'
        DATA = 'DATA', 'Data'

    class Category(models.TextChoices):
        GST = 'GST', 'GST'
        EXCISE = 'EXCISE', 'Excise'
        IMPORT = 'IMPORT', 'Import'
        LEVY = 'LEVY', 'Levy'
        REGULATORY = 'REGULATORY', 'Regulatory'
        OTHER = 'OTHER', 'Other'

    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=30, unique=True)
    category = models.CharField(max_length=15, choices=Category.choices, default=Category.OTHER, db_index=True)
    description = models.TextField(blank=True)
    # List of service types this tax applies to (empty = all services)
    applicable_services = models.JSONField(default=list, blank=True)
    # List of operator codes this tax applies to (empty = all operators)
    applicable_operators = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_tax_types'
        ordering = ['name']

    def __str__(self):
        return self.name


class TaxRate(models.Model):
    """A versioned tax rate for a given TaxType. Multiple rates can exist
    across time; only the one whose effective period contains the target
    date is used. Historical rates are never deleted."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        ACTIVE = 'ACTIVE', 'Active'
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        EXPIRED = 'EXPIRED', 'Expired'
        INACTIVE = 'INACTIVE', 'Inactive'

    class Treatment(models.TextChoices):
        EXCLUSIVE = 'EXCLUSIVE', 'Exclusive'
        INCLUSIVE = 'INCLUSIVE', 'Inclusive'

    tax_type = models.ForeignKey(
        TaxType, on_delete=models.CASCADE, related_name='rates',
    )
    rate_percent = models.DecimalField(max_digits=6, decimal_places=3)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    treatment = models.CharField(max_length=10, choices=Treatment.choices, default=Treatment.EXCLUSIVE)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'regulatory_tax_rates'
        ordering = ['tax_type', '-version']
        indexes = [
            models.Index(fields=['tax_type', 'effective_from', 'effective_to']),
        ]

    def __str__(self):
        return f'{self.tax_type.code} v{self.version} {self.rate_percent}% from {self.effective_from}'

    @property
    def is_current(self):
        from datetime import date
        today = date.today()
        if self.effective_from > today:
            return False
        if self.effective_to and self.effective_to < today:
            return False
        return True
