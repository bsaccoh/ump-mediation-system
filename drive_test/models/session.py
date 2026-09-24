import hashlib
import uuid

from django.conf import settings
from django.db import models

from .geography import Region
from .parsers import ParserProfile


def _generate_session_ref():
    return 'DT-' + uuid.uuid4().hex[:12].upper()


class DriveTestSession(models.Model):
    """
    Top-level container for a drive-test exercise.
    One session may span multiple files (e.g. one file per vehicle per day).
    """

    class Status(models.TextChoices):
        UPLOADING = 'UPLOADING', 'Uploading'
        PENDING = 'PENDING', 'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETED = 'COMPLETED', 'Completed'
        PARTIAL = 'PARTIAL', 'Partial'
        FAILED = 'FAILED', 'Failed'

    class TestType(models.TextChoices):
        OUTDOOR = 'outdoor', 'Outdoor Drive Test'
        INDOOR = 'indoor', 'Indoor Walk Test'
        BENCHMARKING = 'benchmarking', 'Benchmarking'
        REGULATORY = 'regulatory', 'Regulatory Audit'
        COVERAGE = 'coverage', 'Coverage Survey'
        OTHER = 'other', 'Other'

    # UMP-generated reference
    session_ref = models.CharField(max_length=32, unique=True, default=_generate_session_ref)
    operator = models.ForeignKey(
        'reference.Operator', on_delete=models.PROTECT, related_name='drive_test_sessions'
    )
    title = models.CharField(max_length=200, blank=True)
    test_date = models.DateField()
    test_type = models.CharField(max_length=20, choices=TestType.choices, default=TestType.OUTDOOR)

    # Geographic scope
    region = models.ForeignKey(
        Region, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions'
    )

    # Test team
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='uploaded_drive_sessions',
    )
    tester_name = models.CharField(max_length=200, blank=True)
    vehicle_id = models.CharField(max_length=50, blank=True)

    # Status
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Aggregates (refreshed on each file completion)
    total_measurements = models.IntegerField(default=0)
    matched_measurements = models.IntegerField(default=0)
    finding_count = models.IntegerField(default=0)

    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-test_date', '-created_at']
        indexes = [
            models.Index(fields=['operator', 'test_date']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.session_ref} – {self.operator} {self.test_date}'


class DriveTestFile(models.Model):
    """
    An individual file uploaded as part of a DriveTestSession.
    The original file is stored immutably; processing never mutates it.
    SHA-256 is used for duplicate detection across all operators.
    """

    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Received'
        PARSING = 'PARSING', 'Parsing'
        PARSED = 'PARSED', 'Parsed'
        MATCHING = 'MATCHING', 'Cell Matching'
        NORMALIZING = 'NORMALIZING', 'Normalizing'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        DUPLICATE = 'DUPLICATE', 'Duplicate'
        REJECTED = 'REJECTED', 'Rejected'

    session = models.ForeignKey(
        DriveTestSession, on_delete=models.CASCADE, related_name='files'
    )
    # File identity
    original_filename = models.CharField(max_length=500)
    file_path = models.CharField(max_length=1000)  # path under UMP storage root
    file_size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, unique=True)  # dedup key
    mime_type = models.CharField(max_length=100, blank=True)

    # Parser
    parser_profile = models.ForeignKey(
        ParserProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='processed_files',
    )
    detected_format = models.CharField(max_length=50, blank=True)

    # Processing state
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    measurement_count = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)

    # Async job tracking
    job = models.ForeignKey(
        'core.JobRecord', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='drive_test_files',
    )

    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['sha256']),
            models.Index(fields=['session', 'status']),
        ]

    def __str__(self):
        return f'{self.original_filename} ({self.status})'
