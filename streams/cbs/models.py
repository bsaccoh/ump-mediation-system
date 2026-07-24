"""
CBS CDR Record Model
=====================
Stores Huawei CBS (Charging and Billing System) CDR records.
All 8 sub-types (Recharge, Voice, SMS, DATA, MON, CM, Loan, Adjustment)
share this table, discriminated by the cbs_type field.

CBS files arrive as pipe-delimited plain text — no binary decoding required.
All fields (450+) are preserved in raw_data; key business fields are promoted
to indexed DB columns for efficient distribution filtering.
"""
from django.db import models


class CBSRecord(models.Model):
    """A single Huawei CBS CDR record."""

    class CbsType(models.TextChoices):
        RECHARGE   = 'RECHARGE',   'Recharge'
        VOICE      = 'VOICE',      'Voice'
        SMS        = 'SMS',        'SMS'
        DATA       = 'DATA',       'Data'
        MON        = 'MON',        'MON'
        CM         = 'CM',         'CM'
        LOAN       = 'LOAN',       'Loan'
        ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'
        UNKNOWN    = 'UNKNOWN',    'Unknown'

    class Status(models.TextChoices):
        VALID   = 'VALID',   'Valid'
        INVALID = 'INVALID', 'Invalid'

    # --- Relations -----------------------------------------------------------
    file = models.ForeignKey(
        'collection.CDRFile', on_delete=models.CASCADE, db_constraint=False,
        related_name='cbs_records', db_index=True
    )
    source = models.ForeignKey(
        'collection.DataSource', on_delete=models.SET_NULL, db_constraint=False,
        null=True, blank=True, db_index=True
    )

    # --- Discriminator + key business fields (indexed) -----------------------
    cbs_type = models.CharField(
        max_length=20, choices=CbsType.choices,
        default=CbsType.UNKNOWN, db_index=True,
        help_text='CBS CDR sub-type detected from filename or DataSource hint'
    )
    subscriber_id = models.CharField(
        max_length=100, blank=True, db_index=True,
        help_text='Primary subscriber/account identifier (field[0])'
    )
    msisdn = models.CharField(
        max_length=20, blank=True, db_index=True,
        help_text='Mobile subscriber number (field[5])'
    )
    charge_amount = models.DecimalField(
        max_digits=18, decimal_places=6,
        null=True, blank=True,
        help_text='Charge amount if present in the record'
    )
    event_time = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text='Event timestamp parsed from the CDR'
    )

    # --- Full record preservation --------------------------------------------
    # All 450+ fields stored, including empty ones (per operator requirement)
    raw_data = models.JSONField(
        default=dict, blank=True,
        help_text='Complete pipe-delimited record as a header-keyed dict'
    )

    # --- Processing metadata -------------------------------------------------
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.VALID
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'cbs_records'
        ordering = ['-event_time', '-id']
        indexes = [
            models.Index(fields=['cbs_type', 'event_time']),
            models.Index(fields=['file', 'cbs_type']),
            models.Index(fields=['msisdn', 'event_time']),
        ]
        verbose_name = 'CBS CDR Record'
        verbose_name_plural = 'CBS CDR Records'

    def __str__(self):
        return f'{self.cbs_type} | {self.msisdn} | {self.event_time}'
