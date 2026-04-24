"""Reference Data Models — Lookup tables for CDR processing."""
from django.db import models


# =============================================================================
# MCC / MNC
# =============================================================================

class MccMnc(models.Model):
    """Mobile Country Code + Mobile Network Code lookup."""
    mcc = models.CharField(max_length=3)
    mnc = models.CharField(max_length=3)
    country = models.CharField(max_length=100)
    iso = models.CharField(max_length=5, blank=True, help_text='ISO 3166-1 alpha-2 country code')
    dial_code = models.CharField(max_length=10, blank=True, help_text='Country dialling code (e.g. +232)')
    operator = models.CharField(max_length=200)
    is_home = models.BooleanField(default=False, help_text='Home network flag')
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True, auto_now_add=True)
    updated_at = models.DateTimeField(null=True, blank=True, auto_now=True)

    class Meta:
        db_table = 'ref_mcc_mnc'
        unique_together = ('mcc', 'mnc')
        ordering = ['mcc', 'mnc']
        verbose_name = 'MCC/MNC'
        verbose_name_plural = 'MCC/MNC Entries'

    def __str__(self):
        return f'{self.mcc}-{self.mnc} ({self.operator}, {self.country})'

    @property
    def plmn(self):
        return f'{self.mcc}{self.mnc}'


# =============================================================================
# IMSI Prefix
# =============================================================================

class ImsiPrefix(models.Model):
    """IMSI prefix to operator/country mapping."""
    prefix = models.CharField(max_length=15, unique=True, help_text='IMSI prefix (e.g. 61901)')
    mcc = models.CharField(max_length=3, help_text='Mobile Country Code')
    mnc = models.CharField(max_length=3, help_text='Mobile Network Code')
    operator = models.CharField(max_length=200)
    country = models.CharField(max_length=100)
    is_home = models.BooleanField(default=False, help_text='Home network (local SIM)')
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ref_imsi_prefix'
        ordering = ['prefix']
        verbose_name = 'IMSI Prefix'
        verbose_name_plural = 'IMSI Prefixes'

    def __str__(self):
        return f'{self.prefix} → {self.operator} ({self.country})'


# =============================================================================
# Numbering Plan
# =============================================================================

class NumberingPlan(models.Model):
    """E.164 number prefix to operator mapping (numbering plan)."""

    NUMBER_TYPE_CHOICES = [
        ('MOBILE', 'Mobile'),
        ('FIXED', 'Fixed Line'),
        ('PREMIUM', 'Premium Rate'),
        ('TOLLFREE', 'Toll Free'),
        ('SHORTCODE', 'Short Code'),
        ('INTERNATIONAL', 'International'),
        ('SPECIAL', 'Special Service'),
        ('ROAMING', 'Roaming'),
        ('UNKNOWN', 'Unknown'),
    ]

    prefix = models.CharField(max_length=20, unique=True, help_text='Number prefix (e.g. 23276, 23277)')
    country = models.CharField(max_length=100, default='Sierra Leone')
    country_code = models.CharField(max_length=5, blank=True, help_text='Country dialling code (e.g. 232)')
    operator = models.CharField(max_length=200)
    number_type = models.CharField(max_length=20, choices=NUMBER_TYPE_CHOICES, default='MOBILE')
    min_length = models.IntegerField(default=8, help_text='Minimum total number length')
    max_length = models.IntegerField(default=12, help_text='Maximum total number length')
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ref_numbering_plan'
        ordering = ['prefix']
        verbose_name = 'Numbering Plan Entry'
        verbose_name_plural = 'Numbering Plan'

    def __str__(self):
        return f'{self.prefix} → {self.operator} ({self.number_type})'


# =============================================================================
# Trunk Groups
# =============================================================================

class TrunkGroup(models.Model):
    """Trunk group reference for route identification."""

    DIRECTION_CHOICES = [
        ('INCOMING', 'Incoming'),
        ('OUTGOING', 'Outgoing'),
        ('BOTH', 'Bidirectional'),
    ]

    TRUNK_TYPE_CHOICES = [
        ('INTERCONNECT', 'Interconnect'),
        ('ROAMING', 'Roaming / International'),
        ('INTERNAL', 'Internal / Onnet'),
        ('GATEWAY', 'Gateway'),
        ('OTHER', 'Other'),
    ]

    switch_id = models.CharField(max_length=50, blank=True, help_text='MSC/Switch identifier (e.g. FTHMSC1)')
    trunk_id = models.CharField(max_length=50, help_text='Trunk identifier used in CDRs')
    name = models.CharField(max_length=200)
    trunk_type = models.CharField(max_length=20, choices=TRUNK_TYPE_CHOICES, default='INTERCONNECT')
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES, default='BOTH')
    partner = models.CharField(max_length=200, blank=True, help_text='Interconnect partner name')
    country = models.CharField(max_length=100, blank=True)
    prefix = models.CharField(max_length=20, blank=True, help_text='Associated number prefix')
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True, auto_now_add=True)
    updated_at = models.DateTimeField(null=True, blank=True, auto_now=True)

    class Meta:
        db_table = 'ref_trunk_groups'
        unique_together = ('switch_id', 'trunk_id')
        ordering = ['switch_id', 'trunk_id']
        verbose_name = 'Trunk Group'
        verbose_name_plural = 'Trunk Groups'

    def __str__(self):
        prefix = f'{self.switch_id} / ' if self.switch_id else ''
        return f'{prefix}{self.trunk_id} — {self.name} ({self.direction})'


# =============================================================================
# Number Range (kept for backward compatibility)
# =============================================================================

class NumberRange(models.Model):
    """Number range to operator mapping."""
    prefix = models.CharField(max_length=20, unique=True)
    operator = models.CharField(max_length=200)
    country = models.CharField(max_length=100, default='Sierra Leone')
    service_type = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'ref_number_ranges'
        ordering = ['prefix']

    def __str__(self):
        return f'{self.prefix} -> {self.operator}'
