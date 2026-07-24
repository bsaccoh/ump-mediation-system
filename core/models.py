"""
Core Models
============
Shared models used across the entire mediation platform.
"""
from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model for the mediation platform."""

    phone = models.CharField(max_length=20, blank=True)
    department = models.CharField(max_length=100, blank=True)
    is_operator = models.BooleanField(
        default=False,
        help_text='Can upload files and trigger processing',
    )
    is_analyst = models.BooleanField(
        default=False,
        help_text='Can search CDR records and view reports',
    )
    can_lawful_intercept = models.BooleanField(
        default=False,
        help_text='Can open / execute / export lawful-intercept (LEA) requests',
    )

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return self.get_full_name() or self.username


class AuditLog(models.Model):
    """Audit trail for all significant actions."""

    ACTION_CHOICES = [
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('UPLOAD', 'File Upload'),
        ('PROCESS', 'Process'),
        ('EXPORT', 'Export'),
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        # Regulatory events
        ('REGULATORY_REPORT_GENERATED', 'Regulatory Report Generated'),
        ('LEVY_COMPUTED', 'Levy Computed'),
        ('LEVY_PAID', 'Levy Paid'),
        ('LEA_REQUEST_OPENED', 'LEA Request Opened'),
        ('LEA_QUERY_EXECUTED', 'LEA Query Executed'),
        ('LEA_EXPORT', 'LEA Evidentiary Export'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    extra_data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'audit_log'
        ordering = ['-timestamp']
        verbose_name = 'Audit Log Entry'
        verbose_name_plural = 'Audit Log'

    def __str__(self):
        return f'{self.timestamp:%Y-%m-%d %H:%M} | {self.action} | {self.entity_type}'


class Alert(models.Model):
    """System alerts and alarms."""

    SEVERITY_CHOICES = [
        ('INFO', 'Information'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
        ('CRITICAL', 'Critical'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='INFO')
    category = models.CharField(max_length=50, db_index=True)
    source = models.CharField(max_length=100)
    message = models.TextField()
    acknowledged = models.BooleanField(default=False)
    acknowledged_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'alerts'
        ordering = ['-timestamp']

    def __str__(self):
        return f'[{self.severity}] {self.category}: {self.message[:80]}'


class JobRecord(models.Model):
    """Tracks long-running business-engine jobs (rating, invoicing, report
    generation, LEA export, roaming file generation, etc.).

    A view handler enqueues a Celery task, stores its ID + a human-readable
    label in a ``JobRecord`` row with ``status=PENDING``, and redirects to
    a job-status page.  The task wrapper updates the row to RUNNING when
    it starts, SUCCESS/FAILURE when it finishes — with optional ``result``
    (dict) and ``error`` (text).

    The UI polls ``/jobs/<id>/status/`` until terminal state.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        RUNNING = 'RUNNING', 'Running'
        SUCCESS = 'SUCCESS', 'Success'
        FAILURE = 'FAILURE', 'Failure'
        REVOKED = 'REVOKED', 'Cancelled'

    job_type = models.CharField(
        max_length=80, db_index=True,
        help_text='e.g. interconnect.generate_invoice, regulatory.generate_report',
    )
    label = models.CharField(
        max_length=200,
        help_text='Human-readable description shown in the UI',
    )
    status = models.CharField(
        max_length=10, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )

    celery_task_id = models.CharField(max_length=80, blank=True, db_index=True)
    submitted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    params = models.JSONField(default=dict, blank=True,
                                help_text='Input arguments for traceability')
    result = models.JSONField(default=dict, blank=True,
                                help_text='Result payload on success')
    error_message = models.TextField(blank=True)

    progress_pct = models.IntegerField(
        default=0,
        help_text='0-100; engines can update this to show progress',
    )
    progress_message = models.CharField(max_length=200, blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Optional pointer back to the produced entity (Invoice, RoamingFile,
    # RegulatoryReport, LEAExtractionLog) — string FK so we don't need
    # cross-DB ForeignKey machinery.
    result_entity_type = models.CharField(max_length=50, blank=True)
    result_entity_id = models.CharField(max_length=50, blank=True)
    result_url = models.CharField(
        max_length=300, blank=True,
        help_text='Page to navigate to on success (e.g. invoice detail)',
    )

    class Meta:
        db_table = 'job_records'
        ordering = ['-submitted_at']
        verbose_name = 'Job Record'
        verbose_name_plural = 'Job Records'

    def __str__(self):
        return f'#{self.pk} {self.job_type} [{self.status}]'

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.Status.SUCCESS, self.Status.FAILURE, self.Status.REVOKED,
        }

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
