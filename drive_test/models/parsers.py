from django.db import models


class ParserProfile(models.Model):
    """
    Registry entry for a drive-test file format parser.
    The parser_class dotted path is resolved at processing time via importlib.
    """

    name = models.CharField(max_length=100, unique=True)  # e.g. 'NEMO Handy 8.x'
    parser_class = models.CharField(max_length=200)        # dotted Python path
    vendor = models.CharField(max_length=100, blank=True)  # EXFO / Spirent / TEMS / etc.
    # Auto-detection hints
    file_extensions = models.JSONField(default=list, blank=True)  # ['.nemo', '.rwd']
    magic_bytes = models.CharField(max_length=200, blank=True)    # hex prefix pattern
    header_signature = models.CharField(max_length=200, blank=True)
    # Default parser config passed at construction
    default_config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
