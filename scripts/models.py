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


class ScriptRegistry(models.Model):
    """Registry of approved server-side scripts for secure execution.

    Scripts must be installed in the server's script directory and registered
    in this table before they can be used by InputPortal or other components.
    This prevents arbitrary script execution from the UI.
    """
    class ScriptType(models.TextChoices):
        PYTHON = 'PYTHON', 'Python'
        SHELL = 'SHELL', 'Shell / Bash'
        SQL = 'SQL', 'SQL'

    script_id = models.CharField(
        max_length=100, unique=True,
        help_text='Unique identifier for the script (e.g., sftp_orange_pgw)'
    )
    name = models.CharField(
        max_length=200,
        help_text='Human-readable script name'
    )
    purpose = models.TextField(
        blank=True,
        help_text='Description of what the script does'
    )
    script_type = models.CharField(
        max_length=10, choices=ScriptType.choices,
        help_text='Type of script (Python, Shell, SQL)'
    )
    server_path = models.CharField(
        max_length=500,
        help_text='Absolute path to the script on the server (e.g., /opt/ump/scripts/sftp_orange_pgw.py)'
    )
    version = models.CharField(
        max_length=50,
        help_text='Script version (e.g., 1.0.0)'
    )
    checksum = models.CharField(
        max_length=64,
        help_text='SHA-256 checksum of the script file for integrity verification'
    )
    enabled = models.BooleanField(
        default=True,
        help_text='Whether this script is enabled for use'
    )
    allowed_roles = models.JSONField(
        default=list,
        help_text='List of roles allowed to use this script (e.g., ["admin", "operator"])'
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'scripts_script_registry'
        ordering = ['name']
        verbose_name = 'Script Registry'
        verbose_name_plural = 'Script Registry'

    def __str__(self):
        return f'{self.name} ({self.script_id})'

    @staticmethod
    def compute_checksum(path: str) -> str:
        """Return the SHA-256 hex digest of a file on disk."""
        import hashlib
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        return h.hexdigest()

    def verify_checksum(self) -> bool:
        """Return True if the on-disk file matches the registered checksum."""
        import os
        if not os.path.isfile(self.server_path):
            return False
        return self.compute_checksum(self.server_path) == self.checksum

    @classmethod
    def validated_script(cls, script_id: str) -> 'ScriptRegistry':
        """Look up a registered script and verify it is safe to execute.

        Raises ValueError if the script is not registered, disabled, missing
        from disk, or its checksum no longer matches the registry.
        """
        try:
            entry = cls.objects.get(script_id=script_id)
        except cls.DoesNotExist:
            raise ValueError(f'Script "{script_id}" is not registered')
        if not entry.enabled:
            raise ValueError(f'Script "{script_id}" is disabled')
        import os
        if not os.path.isfile(entry.server_path):
            raise ValueError(f'Script file not found: {entry.server_path}')
        if not entry.verify_checksum():
            raise ValueError(
                f'Checksum mismatch for "{script_id}" — '
                f'the file on disk has been modified since registration'
            )
        return entry

