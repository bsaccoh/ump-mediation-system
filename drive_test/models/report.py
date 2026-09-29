"""Generated reports (register + artifact metadata)."""
import uuid

from django.conf import settings
from django.db import models

from .enums import ReportStatus, ReportType
from .project import Campaign, Project


class Report(models.Model):
    ref = models.CharField(max_length=40, unique=True, db_index=True, editable=False)
    report_type = models.CharField(max_length=24, choices=ReportType.choices)
    title = models.CharField(max_length=200, blank=True)

    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name='reports',
    )
    campaign = models.ForeignKey(
        Campaign, null=True, blank=True, on_delete=models.SET_NULL, related_name='reports',
    )

    params = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=12, choices=ReportStatus.choices, default=ReportStatus.PENDING,
    )
    artifact_path = models.CharField(max_length=1024, blank=True)
    artifact_format = models.CharField(max_length=12, blank=True)
    error_message = models.TextField(blank=True)

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='dt_reports',
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-generated_at']

    def save(self, *args, **kwargs):
        if not self.ref:
            self.ref = f'DTR-{uuid.uuid4().hex[:10].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.ref
