"""
Scripts Models
==============
Manages custom scripts (Python, Shell, SQL) that can be configured
and executed within the UMP Mediation System.
"""
from django.conf import settings
from django.db import models


class Script(models.Model):
    class ScriptType(models.TextChoices):
        PYTHON = 'PYTHON', 'Python'
        SHELL  = 'SHELL',  'Shell / Bash'
        SQL    = 'SQL',    'SQL'

    class Status(models.TextChoices):
        ACTIVE   = 'ACTIVE',   'Active'
        INACTIVE = 'INACTIVE', 'Inactive'
        DRAFT    = 'DRAFT',    'Draft'

    class Category(models.TextChoices):
        AM_OUT_OF_BAND    = 'AM_OUT_OF_BAND',    'AM Out-of-Band'
        BATCH_REPAIR      = 'BATCH_REPAIR',      'Batch Repair'
        CORRELATION       = 'CORRELATION',       'Correlation'
        DATA_VISIBILITY   = 'DATA_VISIBILITY',   'Data Visibility'
        DISPLAY           = 'DISPLAY',           'Display'
        DUPLICATE_CHECK   = 'DUPLICATE_CHECK',   'Duplicate Check'
        ERROR_CORRECTION  = 'ERROR_CORRECTION',  'Error Correction'
        FIELD_SECURITY    = 'FIELD_SECURITY',    'Field Security'
        FILE_NAMING       = 'FILE_NAMING',       'File Naming'
        KEY_EXTRACT       = 'KEY_EXTRACT',       'Key Extract'
        LARGE_FILE_INPUT  = 'LARGE_FILE_INPUT',  'Large File Input'
        OUTPUT_PROCESSING = 'OUTPUT_PROCESSING', 'Output Processing'
        RECORD_SEARCH     = 'RECORD_SEARCH',     'Record Search'
        RECORD_VALIDATION = 'RECORD_VALIDATION', 'Record Validation'
        STATISTICS        = 'STATISTICS',        'Statistics'
        SUBROUTINE        = 'SUBROUTINE',        'Subroutine'
        TEST_DATA         = 'TEST_DATA',         'Test Data'
        TRANSFORM         = 'TRANSFORM',         'Transform'

    name        = models.CharField(max_length=150, unique=True)
    category    = models.CharField(max_length=50, choices=Category.choices, default=Category.DISPLAY)
    script_type = models.CharField(max_length=10, choices=ScriptType.choices, default=ScriptType.PYTHON)
    status      = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True)
    content     = models.TextField(help_text='Script source code')
    tags        = models.CharField(max_length=255, blank=True, help_text='Comma-separated tags')
    created_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='scripts')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'scripts_script'
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.script_type})'

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    @property
    def last_run(self):
        return self.executions.order_by('-started_at').first()


class ScriptExecution(models.Model):
    class RunStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        RUNNING = 'RUNNING', 'Running'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED  = 'FAILED',  'Failed'

    script     = models.ForeignKey(Script, on_delete=models.CASCADE, related_name='executions')
    run_by     = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    run_status = models.CharField(max_length=10, choices=RunStatus.choices, default=RunStatus.PENDING)
    output     = models.TextField(blank=True)
    error      = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at= models.DateTimeField(null=True, blank=True)
    duration_ms= models.IntegerField(null=True, blank=True, help_text='Duration in milliseconds')

    class Meta:
        db_table = 'scripts_execution'
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.script.name} @ {self.started_at:%Y-%m-%d %H:%M}'
