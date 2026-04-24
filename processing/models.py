"""
Processing Models
==================
DB-configurable validation and enrichment rules.
These supplement the hardcoded rules in each stream's cdr_fields.py.
"""
from django.db import models


class ValidationRule(models.Model):
    """A configurable field validation rule."""

    name = models.CharField(max_length=100, unique=True)
    stream = models.CharField(max_length=20, help_text='MSC, PGW, SGSN, SGW, or ALL')
    field_name = models.CharField(max_length=100)
    rule_type = models.CharField(max_length=20, choices=[
        ('REQUIRED', 'Required'),
        ('MAX_LENGTH', 'Max Length'),
        ('PATTERN', 'Regex Pattern'),
        ('RANGE', 'Numeric Range'),
    ])
    rule_value = models.CharField(max_length=500)
    error_message = models.CharField(max_length=200, blank=True)
    enabled = models.BooleanField(default=True)
    priority = models.IntegerField(default=100)

    class Meta:
        db_table = 'validation_rules'
        ordering = ['stream', 'priority']

    def __str__(self):
        return f'{self.stream}/{self.field_name}: {self.rule_type}'


class EnrichmentRule(models.Model):
    """A configurable field enrichment rule."""

    name = models.CharField(max_length=100, unique=True)
    stream = models.CharField(max_length=20)
    target_field = models.CharField(max_length=100)
    source_field = models.CharField(max_length=100, blank=True)
    transform = models.CharField(max_length=30, choices=[
        ('COPY', 'Copy from source'),
        ('DEFAULT', 'Set default value'),
        ('FIRST_N', 'First N digits'),
        ('LOOKUP', 'Reference table lookup'),
        ('REGEX_EXTRACT', 'Regex extract'),
    ])
    transform_value = models.CharField(max_length=500, blank=True)
    condition = models.CharField(max_length=30, choices=[
        ('IF_EMPTY', 'Only if target is empty'),
        ('ALWAYS', 'Always apply'),
        ('IF_SMS', 'Only for SMS records'),
    ], default='IF_EMPTY')
    enabled = models.BooleanField(default=True)
    priority = models.IntegerField(default=100)

    class Meta:
        db_table = 'enrichment_rules'
        ordering = ['stream', 'priority']

    def __str__(self):
        return f'{self.stream}/{self.target_field}: {self.transform}'
