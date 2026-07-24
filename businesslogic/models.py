"""
Business Logic Models
=====================
Manages mediation business rules — validation, transformation,
enrichment, filtering, and rating logic applied to CDR streams.
"""
from django.conf import settings
from django.db import models


class BusinessRule(models.Model):
    """A configurable business rule applied during CDR mediation."""

    class RuleType(models.TextChoices):
        VALIDATION     = 'VALIDATION',     'Validation'
        TRANSFORMATION = 'TRANSFORMATION', 'Transformation'
        ENRICHMENT     = 'ENRICHMENT',     'Enrichment'
        FILTERING      = 'FILTERING',      'Filtering'
        RATING         = 'RATING',         'Rating'
        AGGREGATION    = 'AGGREGATION',    'Aggregation'
        CORRELATION    = 'CORRELATION',    'Correlation'
        DETECTION      = 'DETECTION',      'Detection'
        ERROR_HANDLING = 'ERROR_HANDLING', 'Error Handling'

    class Stream(models.TextChoices):
        ALL  = 'ALL',  'All Streams'
        MSC  = 'MSC',  'MSC (Voice/SMS)'
        PGW  = 'PGW',  'PGW (4G Data)'
        SGSN = 'SGSN', 'SGSN (2G/3G)'
        SGW  = 'SGW',  'SGW (4G S-GW)'

    class Status(models.TextChoices):
        ACTIVE   = 'ACTIVE',   'Active'
        INACTIVE = 'INACTIVE', 'Inactive'
        DRAFT    = 'DRAFT',    'Draft'
        TESTING  = 'TESTING',  'Testing'

    class Priority(models.IntegerChoices):
        LOW      = 10,  'Low'
        NORMAL   = 50,  'Normal'
        HIGH     = 80,  'High'
        CRITICAL = 100, 'Critical'

    name         = models.CharField(max_length=200, unique=True)
    rule_type    = models.CharField(max_length=20, choices=RuleType.choices, default=RuleType.VALIDATION)
    stream       = models.CharField(max_length=10, choices=Stream.choices, default=Stream.ALL)
    status       = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    priority     = models.IntegerField(choices=Priority.choices, default=Priority.NORMAL)
    description  = models.TextField(blank=True, help_text='Purpose and behavior of this rule')
    condition    = models.TextField(blank=True, help_text='JSON or expression defining when the rule triggers')
    action       = models.TextField(blank=True, help_text='JSON or expression defining what the rule does')
    tags         = models.CharField(max_length=255, blank=True, help_text='Comma-separated tags')
    created_by   = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='business_rules')
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'businesslogic_rule'
        ordering = ['-priority', 'name']

    def __str__(self):
        return f'{self.name} ({self.get_rule_type_display()})'

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(',') if t.strip()]


class RuleExecutionLog(models.Model):
    """Tracks each time a business rule is evaluated or applied."""

    class Result(models.TextChoices):
        APPLIED = 'APPLIED', 'Applied'
        SKIPPED = 'SKIPPED', 'Skipped'
        FAILED  = 'FAILED',  'Failed'

    rule          = models.ForeignKey(BusinessRule, on_delete=models.CASCADE, related_name='executions')
    result        = models.CharField(max_length=10, choices=Result.choices)
    records_in    = models.IntegerField(default=0, help_text='Records evaluated')
    records_out   = models.IntegerField(default=0, help_text='Records affected / passed')
    message       = models.TextField(blank=True)
    executed_by   = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    executed_at   = models.DateTimeField(auto_now_add=True)
    duration_ms   = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'businesslogic_execution'
        ordering = ['-executed_at']

    def __str__(self):
        return f'{self.rule.name} — {self.result} @ {self.executed_at:%Y-%m-%d %H:%M}'
