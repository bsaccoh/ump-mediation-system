"""Uploaded drive-test log files and their processing lifecycle.

A DriveTestFile is the raw upload plus the metadata detected during profiling.
Its ``job`` links to core.JobRecord so processing progress reuses the existing
platform job-tracking + polling machinery rather than a parallel system.
"""
from django.conf import settings
from django.db import models

from .enums import FileStatus, Technology, ValidationStatus
from .project import Campaign


class DriveTestFile(models.Model):
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='files',
    )
    original_name = models.CharField(max_length=255)
    stored_path = models.CharField(max_length=1024, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    # SHA-256 of the file content, for idempotent re-upload / dedup.
    sha256 = models.CharField(max_length=64, db_index=True, blank=True)

    detected_format = models.CharField(max_length=40, blank=True)
    detected_operator = models.CharField(max_length=60, blank=True)
    detected_technology = models.CharField(
        max_length=8, choices=Technology.choices, blank=True,
    )
    # Tri-state: True / False / None ("not yet known") — never coerced to False.
    gps_available = models.BooleanField(null=True, blank=True)
    sample_count = models.IntegerField(null=True, blank=True)

    validation_status = models.CharField(
        max_length=8, choices=ValidationStatus.choices, default=ValidationStatus.UNKNOWN,
    )
    status = models.CharField(
        max_length=12, choices=FileStatus.choices, default=FileStatus.PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True)

    # Reuse core.JobRecord for background processing + progress polling.
    # db_constraint=False mirrors the platform's cross-app FK convention.
    job = models.ForeignKey(
        'core.JobRecord', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='dt_files', db_constraint=False,
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='dt_files_uploaded',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [models.Index(fields=['campaign', 'status'])]

    def __str__(self):
        return self.original_name
