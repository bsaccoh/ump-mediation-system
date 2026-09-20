from django.conf import settings
from django.db import models


class OperatorDeclaration(models.Model):
    """Periodic revenue/traffic declaration submitted by an operator."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        VALIDATION = 'VALIDATION', 'Validation'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        RECONCILED = 'RECONCILED', 'Reconciled'
        REJECTED = 'REJECTED', 'Rejected'
        CLOSED = 'CLOSED', 'Closed'
        VERIFIED = 'VERIFIED', 'Verified (legacy)'
        DISPUTED = 'DISPUTED', 'Disputed (legacy)'

    class DeclarationType(models.TextChoices):
        GST = 'GST', 'GST'
        REVENUE = 'REVENUE', 'Revenue'
        GST_REVENUE = 'GST_REVENUE', 'GST + Revenue'
        INTERNATIONAL_REVENUE = 'INTERNATIONAL_REVENUE', 'International Revenue'
        ROAMING_REVENUE = 'ROAMING_REVENUE', 'Roaming Revenue'
        INTERCONNECT_REVENUE = 'INTERCONNECT_REVENUE', 'Interconnect Revenue'

    operator_code = models.SlugField(max_length=30, db_index=True)
    reference = models.CharField(max_length=32, unique=True, null=True, blank=True, db_index=True)
    period_start = models.DateField()
    period_end = models.DateField()
    declaration_type = models.CharField(max_length=30, choices=DeclarationType.choices, default=DeclarationType.GST_REVENUE)
    version = models.PositiveIntegerField(default=1)
    parent_declaration = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='amendments')
    is_current_version = models.BooleanField(default=True)
    currency = models.CharField(max_length=3, default='SLE')

    total_declared_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    declared_taxable_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_declared_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    international_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    roaming_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    interconnect_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    other_taxable_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.DRAFT, db_index=True,
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    rejection_reason = models.CharField(max_length=100, blank=True)
    validation_result = models.CharField(max_length=10, blank=True)
    validation_findings = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_declarations'
        ordering = ['-period_start']
        indexes = [
            models.Index(fields=['operator_code', 'period_start']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['operator_code', 'period_start', 'period_end', 'declaration_type', 'version'], name='unique_declaration_version'),
        ]

    def __str__(self):
        return f'{self.operator_code} | {self.period_start} - {self.period_end} [{self.status}]'


class DeclarationLineItem(models.Model):
    """One service-type row within an operator declaration."""

    class ServiceType(models.TextChoices):
        VOICE = 'VOICE', 'Voice'
        SMS = 'SMS', 'SMS'
        DATA = 'DATA', 'Data'
        INTERNATIONAL = 'INTERNATIONAL', 'International'
        ROAMING = 'ROAMING', 'Roaming'
        INTERCONNECT = 'INTERCONNECT', 'Interconnect'
        OTHER = 'OTHER', 'Other'

    declaration = models.ForeignKey(
        OperatorDeclaration, on_delete=models.CASCADE, related_name='line_items',
    )
    service_type = models.CharField(max_length=20, choices=ServiceType.choices)
    declared_traffic_count = models.BigIntegerField(default=0)
    declared_duration_seconds = models.BigIntegerField(default=0)
    declared_data_bytes = models.BigIntegerField(default=0)
    declared_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    declared_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'regulatory_declaration_items'
        ordering = ['declaration', 'service_type']

    def __str__(self):
        return f'{self.declaration.operator_code} | {self.service_type} = {self.declared_revenue}'


class DeclarationAttachment(models.Model):
    declaration = models.ForeignKey(OperatorDeclaration, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='regulatory/declarations/%Y/%m/')
    document_type = models.CharField(max_length=50, default='SUPPORTING_DOCUMENT')
    description = models.CharField(max_length=255, blank=True)
    file_name = models.CharField(max_length=255)
    file_size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regulatory_declaration_attachments'
        ordering = ['-uploaded_at']


class DeclarationReview(models.Model):
    class Action(models.TextChoices):
        VALIDATED = 'VALIDATED', 'Validated'
        REVIEW_STARTED = 'REVIEW_STARTED', 'Review Started'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        REJECTED = 'REJECTED', 'Rejected'
        CLARIFICATION = 'CLARIFICATION', 'Clarification Requested'
        RECONCILED = 'RECONCILED', 'Reconciled'

    declaration = models.ForeignKey(OperatorDeclaration, on_delete=models.CASCADE, related_name='reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    action = models.CharField(max_length=20, choices=Action.choices)
    reason = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regulatory_declaration_reviews'
        ordering = ['-created_at']
