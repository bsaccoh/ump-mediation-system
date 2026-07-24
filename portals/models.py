"""
Portals Models
==============
Defines InputPortal, OutputPortal, Plugin, and Resource models
for the UMP Mediation System.
"""
import json

from django.core.exceptions import ValidationError
from django.db import models


class StreamTypeMixin(models.Model):
    """Abstract mixin providing stream_type choices."""

    class StreamType(models.TextChoices):
        MSC  = 'MSC',  'MSC'
        IMS  = 'IMS',  'IMS (VoLTE/VoBB)'
        PGW  = 'PGW',  'PGW'
        SGSN = 'SGSN', 'SGSN'
        SGW  = 'SGW',  'SGW'
        CBS  = 'CBS',  'CBS (Huawei OCS/CBS)'
        ALL  = 'ALL',  'All Streams'

    stream_type = models.CharField(
        max_length=10,
        choices=StreamType.choices,
        default=StreamType.ALL,
    )

    class Meta:
        abstract = True


class InputPortal(StreamTypeMixin):
    """A configured input source from which CDR files are collected."""

    class PortalType(models.TextChoices):
        FTP = 'FTP', 'FTP'
        SFTP = 'SFTP', 'SFTP'
        LOCAL = 'LOCAL', 'Local Directory'
        API = 'API', 'REST API'

    name = models.CharField(max_length=100)
    portal_type = models.CharField(max_length=10, choices=PortalType.choices)
    host = models.CharField(max_length=255, blank=True)
    port = models.IntegerField(null=True, blank=True)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=255, blank=True)
    directory = models.CharField(max_length=500, blank=True)
    file_pattern = models.CharField(max_length=200, blank=True, default='*.dat')
    polling_interval = models.IntegerField(
        default=300,
        help_text='Polling interval in seconds',
    )
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- FAI / Protocol Parameters ---
    script_name = models.CharField(max_length=200, blank=True)
    connect_timeout = models.IntegerField(default=0, help_text="Connect Timeout (seconds)")
    enable_transcript = models.BooleanField(default=True)
    
    class Disposition(models.TextChoices):
        MOVE = 'MOVE', 'MOVE'
        DELETE = 'DELETE', 'DELETE'
        KEEP = 'KEEP', 'KEEP'
        
    disposition = models.CharField(max_length=10, choices=Disposition.choices, default=Disposition.MOVE)
    rename_ext = models.CharField(max_length=50, blank=True)
    move_to = models.CharField(max_length=500, blank=True)
    rmv_prefix = models.CharField(max_length=50, blank=True)
    reg_pattern = models.CharField(max_length=200, blank=True)

    # --- Dispatch Parameters ---
    dispatch_status = models.BooleanField(default=True)
    
    class DispatchMethod(models.TextChoices):
        SCHEDULED = 'Scheduled', 'Scheduled'
        IMMEDIATE = 'Immediate', 'Immediate'
        
    dispatch_method = models.CharField(max_length=20, choices=DispatchMethod.choices, default=DispatchMethod.SCHEDULED)
    
    class ScheduleType(models.TextChoices):
        INTERVAL = 'Interval', 'Interval'
        DAILY = 'Daily', 'Daily'
        
    schedule_type = models.CharField(max_length=20, choices=ScheduleType.choices, default=ScheduleType.INTERVAL)
    base_time = models.TimeField(null=True, blank=True)
    interval_mins = models.IntegerField(default=3, help_text="Interval in minutes")
    do_not_dispatch_on = models.CharField(max_length=100, blank=True, help_text="Comma-separated days to exclude")
    no_dispatch_intervals = models.JSONField(default=dict, blank=True, help_text="List of start/end time ranges")
    pending_processing_throttling = models.BooleanField(default=False)
    start_throttling_counts = models.IntegerField(default=0)
    end_throttling_counts = models.IntegerField(default=0)

    # --- Input Processing Parameters ---
    duplicate_detection_algo = models.CharField(max_length=50, default='Disabled')
    duplicate_disposition = models.CharField(max_length=100, default='Transfer and Process Normally')
    duplicate_directory = models.CharField(max_length=500, blank=True)
    duplicate_business_logic = models.CharField(max_length=50, default='None')
    file_compression_type = models.CharField(max_length=50, default='No Compression')
    input_script = models.CharField(max_length=50, default='None')
    large_file_access_descriptor = models.CharField(max_length=50, default='None')

    # Meta and original string
    class Meta:
        db_table = 'portals_input_portal'
        ordering = ['name']
        verbose_name = 'Input Portal'
        verbose_name_plural = 'Input Portals'

    def __str__(self):
        return f"{self.name} ({self.portal_type})"


class OutputPortal(StreamTypeMixin):
    """A configured destination to which processed CDRs are delivered."""

    class PortalType(models.TextChoices):
        FTP = 'FTP', 'FTP'
        SFTP = 'SFTP', 'SFTP'
        LOCAL = 'LOCAL', 'Local Directory'
        DATABASE = 'DATABASE', 'Database'
        API = 'API', 'REST API'

    class OutputFormat(models.TextChoices):
        CSV = 'CSV', 'CSV'
        JSON = 'JSON', 'JSON'
        XML = 'XML', 'XML'
        TEXT = 'TEXT', 'Plain Text'
        RAW = 'RAW', 'Raw Binary (No Decoding)'

    name = models.CharField(max_length=100)
    portal_type = models.CharField(max_length=10, choices=PortalType.choices)
    output_format = models.CharField(
        max_length=10,
        choices=OutputFormat.choices,
        default=OutputFormat.CSV,
    )
    host = models.CharField(max_length=255, blank=True)
    port = models.IntegerField(null=True, blank=True)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=255, blank=True)
    directory = models.CharField(
        max_length=500, blank=True,
        help_text='Absolute path OR path relative to project DATA_DIR. '
                  'Supports placeholders: {operator}, {vendor}, {ne}, {stream}, '
                  '{portal}, {YYYY}, {MM}, {DD}. '
                  'If blank, defaults to "{operator}/output/{vendor}/{ne}".'
    )
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'portals_output_portal'
        ordering = ['name']
        verbose_name = 'Output Portal'
        verbose_name_plural = 'Output Portals'

    def __str__(self):
        return f"{self.name} ({self.portal_type})"

    def clean(self):
        super().clean()
        if self.directory:
            self.directory = self.directory.strip('"').strip("'")

    def resolve_directory(self, dt=None, *, operator=None, vendor=None,
                          network_element=None):
        """Returns the absolute output path, resolving placeholders.

        Multi-operator layout: ``{operator}/output/{vendor}/{ne}``. The
        operator/vendor/network_element come from the CDRFile being delivered;
        each falls back to a safe token when unknown so a file is never lost.
        """
        import os
        from django.conf import settings
        from datetime import datetime

        op = (operator or 'unknown').lower()
        vend = (vendor or 'unknown').lower()
        ne = (network_element or self.stream_type or 'unknown').lower()

        directory = (self.directory or '').strip()
        if not directory:
            # Per-operator default tree: {operator}/output/{vendor}/{ne}
            directory = os.path.join(op, 'output', vend, ne)

        dt = dt or datetime.now()
        replacements = {
            '{operator}': op,
            '{vendor}': vend,
            '{ne}': ne,
            '{stream}': self.stream_type.lower(),
            '{portal}': self.name.lower(),
            '{YYYY}': dt.strftime('%Y'),
            '{MM}': dt.strftime('%m'),
            '{DD}': dt.strftime('%d'),
        }
        for placeholder, value in replacements.items():
            directory = directory.replace(placeholder, value)

        if not os.path.isabs(directory):
            directory = os.path.join(settings.DATA_DIR, directory)

        return directory


class Plugin(models.Model):
    """A processing plugin that can be attached to a pipeline."""

    class PluginType(models.TextChoices):
        DECODER = 'DECODER', 'Decoder'
        ENRICHMENT = 'ENRICHMENT', 'Enrichment'
        VALIDATION = 'VALIDATION', 'Validation'
        EXPORT = 'EXPORT', 'Export'
        NOTIFICATION = 'NOTIFICATION', 'Notification'

    name = models.CharField(max_length=100)
    plugin_type = models.CharField(max_length=20, choices=PluginType.choices)
    version = models.CharField(max_length=20, default='1.0.0')
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    config = models.JSONField(default=dict, blank=True)
    author = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'portals_plugin'
        ordering = ['name']
        verbose_name = 'Plugin'
        verbose_name_plural = 'Plugins'

    def __str__(self):
        return f"{self.name} v{self.version}"


class Resource(models.Model):
    """A system resource (worker, queue, storage, etc.) that is monitored."""

    class ResourceType(models.TextChoices):
        WORKER = 'WORKER', 'Processing Worker'
        QUEUE = 'QUEUE', 'Message Queue'
        STORAGE = 'STORAGE', 'Storage'
        DATABASE = 'DATABASE', 'Database'
        CACHE = 'CACHE', 'Cache'

    class Status(models.TextChoices):
        ONLINE = 'ONLINE', 'Online'
        OFFLINE = 'OFFLINE', 'Offline'
        DEGRADED = 'DEGRADED', 'Degraded'
        MAINTENANCE = 'MAINTENANCE', 'Maintenance'

    name = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=15, choices=ResourceType.choices)
    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.ONLINE,
    )
    host = models.CharField(max_length=255, blank=True)
    config = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True)
    is_monitored = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'portals_resource'
        ordering = ['name']
        verbose_name = 'Resource'
        verbose_name_plural = 'Resources'

    def __str__(self):
        return f"{self.name} ({self.resource_type})"


class OutputSchema(StreamTypeMixin):
    """Defines the output file format and header mapping for a specific downstream."""
    name = models.CharField(max_length=100)
    mapping_json = models.JSONField(
        default=dict, 
        help_text="JSON mapping of Output Header -> Database Field (e.g. {'MSISDN': 'subscriber_id'})"
    )
    delimiter = models.CharField(max_length=5, default=',')
    include_header = models.BooleanField(default=True)
    quote_all = models.BooleanField(default=False, help_text="If true, quote all fields in CSV output.")
    line_terminator = models.CharField(
        max_length=5, default='\n', 
        help_text="Line endings: \\n for Unix, \\r\\n for Windows."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portals_output_schema'
        verbose_name = 'Output Schema'
        verbose_name_plural = 'Output Schemas'

    def __str__(self):
        return f"{self.name} ({self.stream_type})"


class DistributionRule(StreamTypeMixin):
    """Maps an input stream to an output portal with specific filtering and schema."""
    name = models.CharField(max_length=100)
    output_portal = models.ForeignKey(OutputPortal, on_delete=models.CASCADE, related_name='rules')
    output_schema = models.ForeignKey(OutputSchema, on_delete=models.PROTECT, related_name='rules')
    
    filter_logic = models.TextField(
        blank=True,
        help_text='JSON dict of field=value applied via Django ORM .filter(**kwargs). '
                  'Empty matches all. Example: {"prepaid_flag": "POSTPAID", "is_roaming": false}'
    )
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=10)

    # Retry policy (per-rule overrides)
    max_retries = models.IntegerField(
        default=3,
        help_text='Total delivery attempts before giving up (1 = no retry).'
    )
    retry_backoff_seconds = models.CharField(
        max_length=100,
        default='2,5',
        blank=True,
        help_text='Comma-separated wait seconds between attempts, e.g. "2,5,10". '
                  'Last value is reused if attempts exceed the list length.'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portals_distribution_rule'
        ordering = ['priority', 'name']
        verbose_name = 'Distribution Rule'
        verbose_name_plural = 'Distribution Rules'

    def __str__(self):
        return f"{self.name} -> {self.output_portal.name}"

    def backoff_schedule(self) -> list:
        """Parse `retry_backoff_seconds` into a list of ints. Returns [] when blank/invalid."""
        text = (self.retry_backoff_seconds or '').strip()
        if not text:
            return []
        out = []
        for part in text.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                v = int(part)
                if v >= 0:
                    out.append(v)
            except ValueError:
                continue
        return out

    def filter_kwargs(self) -> dict:
        text = (self.filter_logic or '').strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def clean(self):
        text = (self.filter_logic or '').strip()
        if not text:
            return
        try:
            data = json.loads(text)
        except (TypeError, ValueError) as e:
            raise ValidationError({'filter_logic': f'Invalid JSON: {e}'})
        if not isinstance(data, dict):
            raise ValidationError({'filter_logic': 'filter_logic must be a JSON object'})
