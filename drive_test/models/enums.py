"""Shared choice vocabularies for the Drive Test Intelligence module.

Kept in one place so the same values are used by models, services, serializers
and templates — never duplicated as string literals.
"""
from django.db import models


class Technology(models.TextChoices):
    GSM = 'GSM', '2G (GSM)'
    UMTS = 'UMTS', '3G (UMTS)'
    LTE = 'LTE', '4G (LTE)'
    NR = 'NR', '5G (NR)'


class ProjectStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    ACTIVE = 'ACTIVE', 'Active'
    PROCESSING = 'PROCESSING', 'Processing'
    COMPLETED = 'COMPLETED', 'Completed'
    ARCHIVED = 'ARCHIVED', 'Archived'


class FileStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    PROFILING = 'PROFILING', 'Profiling'
    READY = 'READY', 'Ready'
    QUEUED = 'QUEUED', 'Queued'
    PROCESSING = 'PROCESSING', 'Processing'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'
    DUPLICATE = 'DUPLICATE', 'Duplicate'


class ValidationStatus(models.TextChoices):
    UNKNOWN = 'UNKNOWN', 'Not validated'
    VALID = 'VALID', 'Valid'
    WARNING = 'WARNING', 'Valid with warnings'
    INVALID = 'INVALID', 'Invalid'


class Severity(models.TextChoices):
    CRITICAL = 'CRITICAL', 'Critical'
    HIGH = 'HIGH', 'High'
    MEDIUM = 'MEDIUM', 'Medium'
    LOW = 'LOW', 'Low'


class EventStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    ACKNOWLEDGED = 'ACKNOWLEDGED', 'Acknowledged'
    RESOLVED = 'RESOLVED', 'Resolved'
    DISMISSED = 'DISMISSED', 'Dismissed'


class MatchMethod(models.TextChoices):
    NONE = 'NONE', 'Not matched'
    EXACT = 'EXACT', 'Exact identifier match'
    PCI_FREQ = 'PCI_FREQ', 'PCI + frequency match'
    NEAREST = 'NEAREST', 'Nearest reference cell'
    MANUAL = 'MANUAL', 'Manually assigned'


class ReportType(models.TextChoices):
    EXECUTIVE = 'EXECUTIVE', 'Executive Report'
    TECHNICAL = 'TECHNICAL', 'Technical RF Report'
    COVERAGE = 'COVERAGE', 'Coverage Report'
    OPERATOR_COMPARISON = 'OPERATOR_COMPARISON', 'Operator Comparison'
    ROUTE = 'ROUTE', 'Route Report'
    CAMPAIGN = 'CAMPAIGN', 'Campaign Report'
    REGULATORY = 'REGULATORY', 'Regulatory Report'


class ReportStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    GENERATING = 'GENERATING', 'Generating'
    READY = 'READY', 'Ready'
    FAILED = 'FAILED', 'Failed'
