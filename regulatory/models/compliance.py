from django.conf import settings
from django.db import models


class TariffComplianceResult(models.Model):
    """A persisted comparison of an operator tariff and its NatCA reference tariff."""

    class Status(models.TextChoices):
        COMPLIANT = 'COMPLIANT', 'Compliant'
        NON_COMPLIANT = 'NON_COMPLIANT', 'Non-Compliant'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'

    applied_tariff = models.ForeignKey(
        'regulatory.Tariff', on_delete=models.CASCADE, related_name='compliance_results',
    )
    approved_tariff = models.ForeignKey(
        'regulatory.Tariff', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reference_compliance_results',
    )
    operator_code = models.SlugField(max_length=30, db_index=True)
    service_type = models.CharField(max_length=10, db_index=True)
    traffic_type = models.CharField(max_length=20, db_index=True)
    tariff_name = models.CharField(max_length=200)
    applied_rate = models.DecimalField(max_digits=12, decimal_places=2)
    approved_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    variance_percent = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tolerance_percent = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=15, choices=Status.choices, db_index=True)
    effective_date = models.DateField(db_index=True)
    last_checked = models.DateTimeField(auto_now=True)
    applied_tariff_version = models.PositiveIntegerField()
    approved_tariff_version = models.PositiveIntegerField(null=True, blank=True)
    finding_created = models.BooleanField(default=False)
    finding_case = models.ForeignKey(
        'regulatory.AuditCase', on_delete=models.SET_NULL, null=True, blank=True, related_name='compliance_results',
    )
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regulatory_tariff_compliance_results'
        ordering = ['-last_checked', 'operator_code', 'tariff_name']
        constraints = [
            models.UniqueConstraint(fields=['applied_tariff', 'effective_date'], name='unique_tariff_compliance_check'),
        ]

    def __str__(self):
        return f'{self.operator_code} | {self.tariff_name} [{self.status}]'
