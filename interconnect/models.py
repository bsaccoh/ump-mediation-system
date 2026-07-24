"""
Interconnect Billing Models
=============================
Configurable partner / rate-card / billing-cycle / invoice / reconciliation /
settlement schema used by the Interconnect Billing module.

Money fields use ``Decimal`` (max_digits=18) to avoid float drift.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# 1. InterconnectPartner — other operators we exchange traffic with
# ---------------------------------------------------------------------------

class InterconnectPartner(models.Model):
    """An operator we have an interconnect agreement with.

    Exactly one row is marked ``is_home=True`` to represent our own network
    (Orange SL).  Every other row is a peer/partner.
    """

    code = models.CharField(
        max_length=20, unique=True,
        help_text='Short code, e.g. ORANG, AFRIC, QCELL, VODAUK',
    )
    name = models.CharField(max_length=120)
    country = models.CharField(max_length=80, default='Sierra Leone')
    country_code = models.CharField(
        max_length=5, blank=True,
        help_text='ITU country code, e.g. 232, 44, 233',
    )
    mcc = models.CharField(max_length=3, blank=True)
    mnc = models.CharField(max_length=3, blank=True)
    is_local = models.BooleanField(default=False, db_index=True,
                                    help_text='Sierra Leone operator (vs foreign carrier)')
    is_home = models.BooleanField(default=False, db_index=True,
                                   help_text='Our own network (Orange) — exactly one row True')
    is_primary_for_country = models.BooleanField(
        default=False, db_index=True,
        help_text=('When multiple partners share a country_code, this flag '
                   'marks the one that receives uncategorised international '
                   'traffic for that CC.'),
    )
    is_roaming_partner = models.BooleanField(
        default=False, db_index=True,
        help_text='True if this operator hosts roamers we settle via TAP-style files.',
    )
    default_currency = models.CharField(max_length=3, default='SLE')

    # Billing contact
    billing_email = models.EmailField(blank=True)
    billing_address = models.TextField(blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)

    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ic_partners'
        ordering = ['-is_home', '-is_local', 'name']
        verbose_name = 'Interconnect Partner'
        verbose_name_plural = 'Interconnect Partners'

    def __str__(self):
        return f'{self.name} ({self.code})'


# ---------------------------------------------------------------------------
# 2. InterconnectRate — rate card (partner × direction × service × tier × time)
# ---------------------------------------------------------------------------

class InterconnectRate(models.Model):
    """A single rate-card entry.  Look-up key:
    (partner, direction, service_type, destination_type, time_of_day, date)."""

    class Direction(models.TextChoices):
        INBOUND  = 'INBOUND',  'Inbound (we charge partner)'
        OUTBOUND = 'OUTBOUND', 'Outbound (partner charges us)'

    class ServiceType(models.TextChoices):
        VOICE = 'VOICE', 'Voice'
        SMS   = 'SMS',   'SMS'
        DATA  = 'DATA',  'Data'
        MMS   = 'MMS',   'MMS'

    class DestinationType(models.TextChoices):
        ON_NET        = 'ON_NET',        'On-net'
        LOCAL         = 'LOCAL',         'Local'
        NATIONAL      = 'NATIONAL',      'National'
        INTERNATIONAL = 'INTERNATIONAL', 'International'
        PREMIUM       = 'PREMIUM',       'Premium'

    class TimeOfDay(models.TextChoices):
        ANY      = 'ANY',      'Any time'
        PEAK     = 'PEAK',     'Peak hours'
        OFF_PEAK = 'OFF_PEAK', 'Off-peak'
        WEEKEND  = 'WEEKEND',  'Weekend'

    class Unit(models.TextChoices):
        PER_MINUTE = 'PER_MINUTE', 'Per minute'
        PER_SECOND = 'PER_SECOND', 'Per second'
        PER_SMS    = 'PER_SMS',    'Per SMS'
        PER_MB     = 'PER_MB',     'Per MB'
        FLAT       = 'FLAT',       'Flat fee per event'

    partner = models.ForeignKey(InterconnectPartner, on_delete=models.CASCADE,
                                 related_name='rates')
    direction = models.CharField(max_length=10, choices=Direction.choices,
                                  db_index=True)
    service_type = models.CharField(max_length=8, choices=ServiceType.choices,
                                     db_index=True)
    destination_type = models.CharField(max_length=15,
                                         choices=DestinationType.choices,
                                         default=DestinationType.NATIONAL)
    rat_filter = models.CharField(max_length=20, blank=True,
                                   help_text='Optional: EUTRAN/UTRAN/GERAN for data rates')
    time_of_day = models.CharField(max_length=10, choices=TimeOfDay.choices,
                                    default=TimeOfDay.ANY)
    unit = models.CharField(max_length=12, choices=Unit.choices,
                             default=Unit.PER_MINUTE)
    rate = models.DecimalField(max_digits=18, decimal_places=6,
                                help_text='Rate per unit')
    min_charge = models.DecimalField(max_digits=18, decimal_places=6,
                                      default=Decimal('0'))
    setup_fee = models.DecimalField(max_digits=18, decimal_places=6,
                                     default=Decimal('0'),
                                     help_text='One-off per call/session')
    currency = models.CharField(max_length=3, default='SLE')

    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(null=True, blank=True,
                                     help_text='Open-ended if blank')

    call_type = models.CharField(max_length=20, blank=True, null=True,
                                 help_text='Optional: Specific call type (e.g., MOC, GWOUT, SMSMO)')
    is_roaming = models.BooleanField(
        default=False, db_index=True,
        help_text='True for TAP-style roaming rates (inbound/outbound roamers).',
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ic_rates'
        ordering = ['partner', '-effective_from', 'direction', 'service_type']
        indexes = [
            models.Index(fields=['partner', 'direction', 'service_type', 'effective_from']),
        ]
        verbose_name = 'Interconnect Rate'
        verbose_name_plural = 'Interconnect Rates'

    def __str__(self):
        return (f'{self.partner.code} {self.direction} {self.service_type} '
                f'{self.destination_type} = {self.rate}/{self.unit}')


# ---------------------------------------------------------------------------
# 3. ExchangeRate — FX table
# ---------------------------------------------------------------------------

class ExchangeRate(models.Model):
    """Point-in-time FX rate.  Invoice generation snapshots the rate at
    issue time so historical invoices don't shift."""

    class Source(models.TextChoices):
        MANUAL       = 'MANUAL',       'Manual entry'
        CENTRAL_BANK = 'CENTRAL_BANK', 'Central Bank'
        XE           = 'XE',           'XE.com'
        OANDA        = 'OANDA',        'OANDA'
        OTHER        = 'OTHER',        'Other'

    from_currency = models.CharField(max_length=3, db_index=True)
    to_currency = models.CharField(max_length=3, db_index=True)
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    effective_date = models.DateField(db_index=True)
    source = models.CharField(max_length=15, choices=Source.choices,
                               default=Source.MANUAL)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )

    class Meta:
        db_table = 'ic_exchange_rates'
        ordering = ['-effective_date', 'from_currency']
        unique_together = [('from_currency', 'to_currency', 'effective_date')]
        verbose_name = 'Exchange Rate'
        verbose_name_plural = 'Exchange Rates'

    def __str__(self):
        return (f'{self.from_currency}→{self.to_currency} '
                f'{self.rate} ({self.effective_date})')


# ---------------------------------------------------------------------------
# 4. BillingCycle — settlement window per partner
# ---------------------------------------------------------------------------

class BillingCycle(models.Model):
    """A monthly (or arbitrary) settlement window for one partner."""

    class Status(models.TextChoices):
        OPEN       = 'OPEN',       'Open'
        PROCESSING = 'PROCESSING', 'Processing'
        CLOSED     = 'CLOSED',     'Closed (rated)'
        INVOICED   = 'INVOICED',   'Invoiced'
        SETTLED    = 'SETTLED',    'Settled'

    partner = models.ForeignKey(InterconnectPartner, on_delete=models.CASCADE,
                                 related_name='cycles')
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                               default=Status.OPEN, db_index=True)

    # Our-side aggregates (filled by rating engine when cycle is closed)
    our_voice_minutes = models.DecimalField(max_digits=18, decimal_places=3,
                                              default=Decimal('0'))
    our_voice_calls = models.IntegerField(default=0)
    our_sms = models.IntegerField(default=0)
    our_data_mb = models.DecimalField(max_digits=18, decimal_places=3,
                                        default=Decimal('0'))

    # Partner-side aggregates (filled by reconciliation upload)
    partner_voice_minutes = models.DecimalField(max_digits=18, decimal_places=3,
                                                  default=Decimal('0'))
    partner_voice_calls = models.IntegerField(default=0)
    partner_sms = models.IntegerField(default=0)
    partner_data_mb = models.DecimalField(max_digits=18, decimal_places=3,
                                            default=Decimal('0'))
    variance_pct = models.DecimalField(max_digits=8, decimal_places=2,
                                         default=Decimal('0'))

    is_roaming = models.BooleanField(
        default=False, db_index=True,
        help_text='True if this cycle settles via TAP-style roaming files.',
    )

    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'ic_billing_cycles'
        ordering = ['-period_end', 'partner']
        unique_together = [('partner', 'period_start', 'period_end')]
        verbose_name = 'Billing Cycle'
        verbose_name_plural = 'Billing Cycles'

    def __str__(self):
        return f'{self.partner.code} {self.period_start} – {self.period_end} [{self.status}]'


# ---------------------------------------------------------------------------
# 5. Invoice
# ---------------------------------------------------------------------------

class Invoice(models.Model):
    """Settlement invoice header for one partner × cycle × direction."""

    class Direction(models.TextChoices):
        INBOUND  = 'INBOUND',  'Inbound (issued to partner)'
        OUTBOUND = 'OUTBOUND', 'Outbound (received from partner)'

    class Status(models.TextChoices):
        DRAFT     = 'DRAFT',     'Draft'
        ISSUED    = 'ISSUED',    'Issued'
        SENT      = 'SENT',      'Sent'
        PAID      = 'PAID',      'Paid'
        PART_PAID = 'PART_PAID', 'Partially Paid'
        OVERDUE   = 'OVERDUE',   'Overdue'
        DISPUTED  = 'DISPUTED',  'Disputed'
        VOID      = 'VOID',      'Voided'

    partner = models.ForeignKey(InterconnectPartner, on_delete=models.PROTECT,
                                 related_name='invoices')
    billing_cycle = models.ForeignKey(BillingCycle, on_delete=models.PROTECT,
                                        related_name='invoices')
    direction = models.CharField(max_length=10, choices=Direction.choices,
                                  default=Direction.INBOUND, db_index=True)
    invoice_number = models.CharField(max_length=40, unique=True)

    subtotal_voice = models.DecimalField(max_digits=18, decimal_places=6,
                                           default=Decimal('0'))
    subtotal_sms = models.DecimalField(max_digits=18, decimal_places=6,
                                         default=Decimal('0'))
    subtotal_data = models.DecimalField(max_digits=18, decimal_places=6,
                                          default=Decimal('0'))
    discount = models.DecimalField(max_digits=18, decimal_places=6,
                                     default=Decimal('0'))
    tax = models.DecimalField(max_digits=18, decimal_places=6,
                                default=Decimal('0'))
    total = models.DecimalField(max_digits=18, decimal_places=6,
                                  default=Decimal('0'))

    currency = models.CharField(max_length=3, default='SLE')
    fx_rate_to_local = models.DecimalField(max_digits=18, decimal_places=8,
                                             default=Decimal('1'))
    total_local = models.DecimalField(max_digits=18, decimal_places=6,
                                        default=Decimal('0'))

    status = models.CharField(max_length=10, choices=Status.choices,
                                default=Status.DRAFT, db_index=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    pdf_file = models.FileField(upload_to='interconnect/invoices/',
                                  blank=True, null=True)
    csv_file = models.FileField(upload_to='interconnect/invoices/',
                                  blank=True, null=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )

    class Meta:
        db_table = 'ic_invoices'
        ordering = ['-created_at']
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'

    def __str__(self):
        return f'{self.invoice_number} ({self.partner.code} {self.direction})'

    @property
    def amount_paid(self):
        """Sum of all settlements applied to this invoice."""
        from django.db.models import Sum
        agg = self.settlements.aggregate(s=Sum('amount'))
        return agg['s'] or Decimal('0')

    @property
    def amount_outstanding(self):
        return self.total - self.amount_paid


# ---------------------------------------------------------------------------
# 6. InvoiceLine
# ---------------------------------------------------------------------------

class InvoiceLine(models.Model):
    """One line on an invoice — one (service, destination, time-of-day) bucket."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                 related_name='lines')
    rate = models.ForeignKey(InterconnectRate, on_delete=models.PROTECT,
                              null=True, blank=True, related_name='+')

    service_type = models.CharField(max_length=8)
    destination_type = models.CharField(max_length=15, blank=True)
    time_of_day = models.CharField(max_length=10, blank=True)

    volume = models.DecimalField(max_digits=18, decimal_places=3,
                                   help_text='Minutes / messages / MB')
    event_count = models.IntegerField(default=0,
                                        help_text='Number of CDRs aggregated')
    unit = models.CharField(max_length=12, default='PER_MINUTE')
    unit_rate = models.DecimalField(max_digits=18, decimal_places=6)
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    currency = models.CharField(max_length=3, default='SLE')

    description = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'ic_invoice_lines'
        ordering = ['invoice', 'service_type', 'destination_type']
        verbose_name = 'Invoice Line'
        verbose_name_plural = 'Invoice Lines'

    def __str__(self):
        return f'{self.invoice_id} {self.service_type}/{self.destination_type} {self.amount}'


# ---------------------------------------------------------------------------
# 7. ReconciliationRecord — our vs partner volumes
# ---------------------------------------------------------------------------

class ReconciliationRecord(models.Model):
    """One reconciliation bucket per (partner, cycle, service, destination)."""

    class Status(models.TextChoices):
        OPEN     = 'OPEN',     'Open'
        MATCHED  = 'MATCHED',  'Matched (within tolerance)'
        DISPUTED = 'DISPUTED', 'Disputed'
        RESOLVED = 'RESOLVED', 'Resolved'

    partner = models.ForeignKey(InterconnectPartner, on_delete=models.CASCADE,
                                 related_name='reconciliations')
    billing_cycle = models.ForeignKey(BillingCycle, on_delete=models.CASCADE,
                                        related_name='reconciliations')
    service_type = models.CharField(max_length=8)
    destination_type = models.CharField(max_length=15, blank=True)

    our_volume = models.DecimalField(max_digits=18, decimal_places=3,
                                       default=Decimal('0'))
    our_amount = models.DecimalField(max_digits=18, decimal_places=6,
                                       default=Decimal('0'))
    partner_volume = models.DecimalField(max_digits=18, decimal_places=3,
                                           default=Decimal('0'))
    partner_amount = models.DecimalField(max_digits=18, decimal_places=6,
                                           default=Decimal('0'))

    variance_volume = models.DecimalField(max_digits=18, decimal_places=3,
                                            default=Decimal('0'))
    variance_amount = models.DecimalField(max_digits=18, decimal_places=6,
                                            default=Decimal('0'))
    variance_pct = models.DecimalField(max_digits=8, decimal_places=2,
                                         default=Decimal('0'))

    status = models.CharField(max_length=10, choices=Status.choices,
                                default=Status.OPEN, db_index=True)
    partner_file_ref = models.CharField(max_length=200, blank=True,
                                          help_text='Source file or row reference')
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ic_reconciliation'
        ordering = ['billing_cycle', 'service_type', 'destination_type']
        unique_together = [('billing_cycle', 'service_type', 'destination_type')]
        verbose_name = 'Reconciliation Record'
        verbose_name_plural = 'Reconciliation Records'

    def __str__(self):
        return (f'{self.partner.code} {self.service_type}/{self.destination_type} '
                f'variance={self.variance_pct}%')


# ---------------------------------------------------------------------------
# 8. Settlement — payment events against an invoice
# ---------------------------------------------------------------------------

class Settlement(models.Model):
    """One payment event (may be partial)."""

    class PaymentMethod(models.TextChoices):
        WIRE   = 'WIRE',   'Bank wire'
        SWIFT  = 'SWIFT',  'SWIFT MT103'
        MPESA  = 'MPESA',  'Mobile money'
        CHEQUE = 'CHEQUE', 'Cheque'
        OFFSET = 'OFFSET', 'Offset (netting against outbound)'
        OTHER  = 'OTHER',  'Other'

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                 related_name='settlements')
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    currency = models.CharField(max_length=3, default='SLE')
    fx_rate_to_local = models.DecimalField(max_digits=18, decimal_places=8,
                                             default=Decimal('1'))
    amount_local = models.DecimalField(max_digits=18, decimal_places=6,
                                         default=Decimal('0'))

    payment_date = models.DateField(db_index=True)
    payment_method = models.CharField(max_length=10,
                                        choices=PaymentMethod.choices,
                                        default=PaymentMethod.WIRE)
    payment_reference = models.CharField(max_length=120, blank=True,
                                           help_text='Bank ref / SWIFT ID / M-Pesa code')
    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
        db_constraint=False,  # User lives in ump_mediation; no cross-DB constraint
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ic_settlements'
        ordering = ['-payment_date', '-created_at']
        verbose_name = 'Settlement'
        verbose_name_plural = 'Settlements'

    def __str__(self):
        return f'{self.invoice.invoice_number} {self.amount} {self.currency} on {self.payment_date}'
