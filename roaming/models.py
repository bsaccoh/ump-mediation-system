"""
Roaming Models
===============
Tables specific to TAP-style inbound-roaming settlement.

Inbound roamers = foreign subscribers on Orange SL's network.  Their CDRs
flow into the regular MSC / IMS / PGW streams; we identify them by IMSI
MCC+MNC ≠ home (619/03) and bill the visited-network charges back to their
home operator via a TAP-equivalent settlement file.

Reuses ``interconnect.models``:

* ``InterconnectPartner`` — set ``is_roaming_partner=True`` for partners
  whose subscribers visit our network.
* ``InterconnectRate``    — set ``is_roaming=True`` for roaming rate cards.
* ``BillingCycle``        — set ``is_roaming=True`` to differentiate from
  regular interconnect settlement.

This app adds two new tables:

* ``RoamingFile``         — one generated TAP-equivalent file per cycle.
* ``RoamingDispute``      — partner-raised disputes against a file.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# 1. RoamingFile — generated TAP-equivalent CSV per (partner, cycle)
# ---------------------------------------------------------------------------

class RoamingFile(models.Model):
    """One TAP-equivalent settlement file generated for an inbound roaming
    partner over a billing period."""

    class Direction(models.TextChoices):
        INBOUND  = 'INBOUND',  'Inbound roamers (we are visited PLMN)'
        OUTBOUND = 'OUTBOUND', 'Outbound roamers (we are home PLMN)'

    class Status(models.TextChoices):
        DRAFT      = 'DRAFT',      'Draft'
        FINAL      = 'FINAL',      'Final'
        SENT       = 'SENT',       'Sent to partner'
        DISPUTED   = 'DISPUTED',   'Disputed'
        SETTLED    = 'SETTLED',    'Settled'

    partner = models.ForeignKey(
        'interconnect.InterconnectPartner', on_delete=models.PROTECT,
        related_name='roaming_files',
        db_constraint=False,  # interconnect lives in a different DB
    )
    billing_cycle = models.ForeignKey(
        'interconnect.BillingCycle', on_delete=models.PROTECT,
        related_name='roaming_files',
        db_constraint=False,  # interconnect lives in a different DB
    )
    direction = models.CharField(max_length=10, choices=Direction.choices,
                                  default=Direction.INBOUND, db_index=True)
    file_number = models.CharField(max_length=40, unique=True,
                                     help_text='TAP-style file ref, e.g. CDxxxx-IN-AFRIC-202603-1')

    # Aggregates captured at generation time
    record_count = models.IntegerField(default=0)
    voice_minutes = models.DecimalField(max_digits=18, decimal_places=3,
                                          default=Decimal('0'))
    sms_count = models.IntegerField(default=0)
    data_mb = models.DecimalField(max_digits=18, decimal_places=3,
                                    default=Decimal('0'))
    total_amount = models.DecimalField(max_digits=18, decimal_places=2,
                                         default=Decimal('0'))
    currency = models.CharField(max_length=3, default='SLE')
    sdr_rate = models.DecimalField(max_digits=18, decimal_places=6,
                                     default=Decimal('1'),
                                     help_text='SDR conversion (Special Drawing Rights) snapshot')

    status = models.CharField(max_length=10, choices=Status.choices,
                                default=Status.DRAFT, db_index=True)

    csv_file = models.FileField(upload_to='roaming/files/', blank=True, null=True)
    sha256 = models.CharField(max_length=64, blank=True,
                                help_text='SHA-256 of the generated file (chain of custody)')

    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'roaming_files'
        ordering = ['-generated_at']
        verbose_name = 'Roaming File'
        verbose_name_plural = 'Roaming Files'

    def __str__(self):
        return f'{self.file_number} ({self.partner.code}, {self.status})'


# ---------------------------------------------------------------------------
# 2. RoamingDispute — partner-raised dispute against a roaming file
# ---------------------------------------------------------------------------

class RoamingDispute(models.Model):
    """A claim from a roaming partner that line items in a file are wrong."""

    class Status(models.TextChoices):
        OPEN         = 'OPEN',         'Open'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under review'
        ACCEPTED     = 'ACCEPTED',     'Accepted (our side)'
        REJECTED     = 'REJECTED',     'Rejected'
        RESOLVED     = 'RESOLVED',     'Resolved'

    roaming_file = models.ForeignKey(
        RoamingFile, on_delete=models.CASCADE,
        related_name='disputes',
    )
    dispute_ref = models.CharField(max_length=60, unique=True,
                                     help_text='Partner-supplied dispute reference')
    raised_by = models.CharField(max_length=120,
                                   help_text='Partner contact name / email')

    claimed_volume = models.DecimalField(max_digits=18, decimal_places=3,
                                            default=Decimal('0'))
    claimed_amount = models.DecimalField(max_digits=18, decimal_places=2,
                                            default=Decimal('0'))
    variance_amount = models.DecimalField(max_digits=18, decimal_places=2,
                                             default=Decimal('0'))
    description = models.TextField(blank=True)

    status = models.CharField(max_length=15, choices=Status.choices,
                                default=Status.OPEN, db_index=True)
    resolution_notes = models.TextField(blank=True)

    opened_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )

    class Meta:
        db_table = 'roaming_disputes'
        ordering = ['-opened_at']
        verbose_name = 'Roaming Dispute'
        verbose_name_plural = 'Roaming Disputes'

    def __str__(self):
        return f'{self.dispute_ref} ({self.get_status_display()})'
