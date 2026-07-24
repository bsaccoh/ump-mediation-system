"""Invoice generation engine.

``generate_invoice(cycle, direction, user)`` orchestrates:

1. Call :func:`interconnect.engines.rating.apply_rates` on the cycle.
2. Filter buckets to the requested direction (INBOUND we charge the partner,
   OUTBOUND partner charges us).
3. Create one ``Invoice`` header + one ``InvoiceLine`` per bucket.
4. Snapshot the FX rate to the home currency (SLE) at issue time.
5. Render a PDF + CSV and attach them to the invoice's ``pdf_file`` /
   ``csv_file`` fields.

The PDF uses ``reportlab.platypus`` so it works on Windows / Linux without
any system fonts beyond Helvetica.
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from ..models import (
    BillingCycle, ExchangeRate, Invoice, InvoiceLine, InterconnectPartner,
)
from .rating import apply_rates, RatingResult


HOME_CURRENCY = 'SLE'
DEFAULT_DUE_DAYS = 30
TWO_PLACES = Decimal('0.01')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_invoice_number(partner, period_end: date, direction: str) -> str:
    """Generate INV-{code}-{YYYYMM}-{IN|OUT}[-N] — unique per (partner, month, dir)."""
    base = f'INV-{partner.code}-{period_end.strftime("%Y%m")}-{direction[:3]}'
    if not Invoice.objects.filter(invoice_number=base).exists():
        return base
    # Suffix -2, -3, … if regenerating
    i = 2
    while Invoice.objects.filter(invoice_number=f'{base}-{i}').exists():
        i += 1
    return f'{base}-{i}'


def _fx_rate(from_currency: str, when: date) -> Decimal:
    """Latest ExchangeRate from→SLE on or before ``when``.  Falls back to 1.0."""
    if from_currency == HOME_CURRENCY:
        return Decimal('1')
    fx = (ExchangeRate.objects
          .filter(from_currency=from_currency, to_currency=HOME_CURRENCY,
                  effective_date__lte=when)
          .order_by('-effective_date').first())
    return fx.rate if fx else Decimal('1')


def _money(value) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

@transaction.atomic
def generate_invoice(cycle: BillingCycle, direction: str = 'INBOUND',
                      user=None) -> Invoice:
    """Run rating, build Invoice + InvoiceLine rows, attach PDF + CSV."""
    if direction not in ('INBOUND', 'OUTBOUND'):
        raise ValueError(f'Invalid direction: {direction!r}')

    result: RatingResult = apply_rates(cycle, persist=True)

    # Filter buckets to this direction
    buckets = [b for b in result.buckets.values() if b.direction == direction]
    if not buckets:
        raise ValueError(
            f'No rated {direction} traffic for {cycle.partner.code} '
            f'in {cycle.period_start}..{cycle.period_end}'
        )

    # Currency = first bucket's rate currency (all rates in a partner cycle
    # should share a currency).  Fall back to partner.default_currency.
    invoice_currency = buckets[0].currency or cycle.partner.default_currency
    fx_to_local = _fx_rate(invoice_currency, cycle.period_end)

    invoice = Invoice.objects.create(
        partner=cycle.partner,
        billing_cycle=cycle,
        direction=direction,
        invoice_number=_next_invoice_number(cycle.partner, cycle.period_end, direction),
        currency=invoice_currency,
        fx_rate_to_local=fx_to_local,
        status=Invoice.Status.DRAFT,
        due_date=cycle.period_end + timedelta(days=DEFAULT_DUE_DAYS),
        created_by=user if user and user.is_authenticated else None,
    )

    sub_voice = sub_sms = sub_data = Decimal('0')
    for b in buckets:
        InvoiceLine.objects.create(
            invoice=invoice,
            rate=b.rate,
            service_type=b.service_type,
            destination_type=b.destination_type,
            time_of_day=b.time_of_day,
            volume=b.volume,
            event_count=b.event_count,
            unit=b.unit,
            unit_rate=b.rate.rate if b.rate else Decimal('0'),
            amount=b.amount,
            currency=b.currency,
            description=(f'{b.service_type} {b.destination_type} '
                          f'{b.time_of_day} ({b.event_count} events)'),
        )
        if b.service_type == 'VOICE':
            sub_voice += b.amount
        elif b.service_type == 'SMS':
            sub_sms += b.amount
        elif b.service_type == 'DATA':
            sub_data += b.amount

    invoice.subtotal_voice = _money(sub_voice)
    invoice.subtotal_sms = _money(sub_sms)
    invoice.subtotal_data = _money(sub_data)
    invoice.total = _money(sub_voice + sub_sms + sub_data)
    invoice.total_local = _money(invoice.total * fx_to_local)
    invoice.save()

    # Flip cycle status if appropriate
    if cycle.status in (BillingCycle.Status.CLOSED, BillingCycle.Status.OPEN):
        cycle.status = BillingCycle.Status.INVOICED
        cycle.save(update_fields=['status'])

    # Attach artefacts
    try:
        pdf_bytes = render_invoice_pdf(invoice)
        invoice.pdf_file.save(f'{invoice.invoice_number}.pdf',
                                ContentFile(pdf_bytes), save=False)
    except Exception:
        pass  # PDF is nice-to-have — never block invoice creation

    csv_bytes = render_invoice_csv(invoice)
    invoice.csv_file.save(f'{invoice.invoice_number}.csv',
                            ContentFile(csv_bytes), save=False)

    invoice.save(update_fields=['pdf_file', 'csv_file'])
    return invoice


# ---------------------------------------------------------------------------
# PDF rendering — reportlab platypus, single page A4
# ---------------------------------------------------------------------------

def render_invoice_pdf(invoice: Invoice) -> bytes:
    """Render a one-page A4 invoice as bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             leftMargin=18 * mm, rightMargin=18 * mm,
                             topMargin=18 * mm, bottomMargin=18 * mm,
                             title=invoice.invoice_number)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=16,
                          spaceAfter=2, textColor=colors.HexColor('#FF6600'))
    h2 = ParagraphStyle('h2', parent=styles['Heading3'], fontSize=11,
                          spaceAfter=2)
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8.5)
    normal = styles['Normal']

    story = []

    # ---- Header
    story.append(Paragraph('ORANGE SIERRA LEONE', h1))
    story.append(Paragraph('Interconnect Settlement Invoice', h2))
    story.append(Spacer(1, 6 * mm))

    # ---- Two-column block: From / To
    issued = (invoice.issued_at or timezone.now()).strftime('%Y-%m-%d')
    home = InterconnectPartner.objects.filter(is_home=True).first()
    from_lines = [Paragraph('<b>From</b>', small)]
    if home:
        from_lines += [Paragraph(home.name, normal),
                        Paragraph(home.billing_address or '', small),
                        Paragraph(home.billing_email or '', small)]
    to_lines = [Paragraph('<b>To</b>', small),
                  Paragraph(invoice.partner.name, normal),
                  Paragraph(invoice.partner.billing_address or '', small),
                  Paragraph(invoice.partner.billing_email or '', small),
                  Paragraph(invoice.partner.contact_name or '', small)]
    meta_lines = [Paragraph('<b>Invoice</b>', small),
                    Paragraph(f'<b>{invoice.invoice_number}</b>', normal),
                    Paragraph(f'Issued: {issued}', small),
                    Paragraph(f'Due: {invoice.due_date or "—"}', small),
                    Paragraph(f'Cycle: {invoice.billing_cycle.period_start} → '
                              f'{invoice.billing_cycle.period_end}', small),
                    Paragraph(f'Direction: {invoice.get_direction_display()}', small)]
    block = Table([[from_lines, to_lines, meta_lines]],
                   colWidths=[55 * mm, 55 * mm, 60 * mm])
    block.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(block)
    story.append(Spacer(1, 8 * mm))

    # ---- Line items
    rows = [['#', 'Service', 'Destination', 'ToD',
              'Events', 'Volume', 'Unit', 'Unit Rate', 'Amount']]
    for i, line in enumerate(invoice.lines.all(), start=1):
        rows.append([
            str(i), line.service_type, line.destination_type or '-',
            line.time_of_day or '-',
            f'{line.event_count:,}',
            f'{line.volume:.3f}',
            line.unit.replace('PER_', '').lower(),
            f'{line.unit_rate:.6f}',
            f'{line.amount:,.2f}',
        ])
    line_tbl = Table(rows, colWidths=[8*mm, 18*mm, 22*mm, 14*mm,
                                       18*mm, 22*mm, 14*mm, 22*mm, 28*mm])
    line_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#FFE5CC')),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (4, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F7F7F7')]),
    ]))
    story.append(line_tbl)
    story.append(Spacer(1, 6 * mm))

    # ---- Totals block (right-aligned)
    totals = [
        ['Voice subtotal:',    f'{invoice.subtotal_voice:,.2f} {invoice.currency}'],
        ['SMS subtotal:',      f'{invoice.subtotal_sms:,.2f} {invoice.currency}'],
        ['Data subtotal:',     f'{invoice.subtotal_data:,.2f} {invoice.currency}'],
        ['Discount:',          f'{invoice.discount:,.2f} {invoice.currency}'],
        ['Tax:',               f'{invoice.tax:,.2f} {invoice.currency}'],
        ['TOTAL DUE:',         f'{invoice.total:,.2f} {invoice.currency}'],
    ]
    if invoice.currency != HOME_CURRENCY:
        totals.append([f'@ FX {invoice.fx_rate_to_local}:',
                        f'{invoice.total_local:,.2f} {HOME_CURRENCY}'])
    tot_tbl = Table(totals, colWidths=[40 * mm, 50 * mm], hAlign='RIGHT')
    tot_tbl.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -2 if invoice.currency != HOME_CURRENCY else -1),
                      (-1, -2 if invoice.currency != HOME_CURRENCY else -1), 0.5, colors.black),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    story.append(tot_tbl)
    story.append(Spacer(1, 10 * mm))

    # ---- Footer
    story.append(Paragraph(
        f'Payment due within {DEFAULT_DUE_DAYS} days. Reference '
        f'<b>{invoice.invoice_number}</b> on all remittances.',
        small,
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        'Disputes must be raised within 30 days of issue. '
        'Generated by UMP Mediation System.',
        small,
    ))

    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CSV rendering
# ---------------------------------------------------------------------------

def render_invoice_csv(invoice: Invoice) -> bytes:
    """Line-item CSV with a header block (for partner reconciliation)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    # Header block (commented-style)
    w.writerow(['# Invoice', invoice.invoice_number])
    w.writerow(['# Partner', invoice.partner.code, invoice.partner.name])
    w.writerow(['# Cycle', invoice.billing_cycle.period_start,
                invoice.billing_cycle.period_end])
    w.writerow(['# Direction', invoice.direction])
    w.writerow(['# Currency', invoice.currency,
                'FX to ' + HOME_CURRENCY, str(invoice.fx_rate_to_local)])
    w.writerow(['# Total', f'{invoice.total:.2f}', invoice.currency])
    w.writerow([])
    # Line items
    w.writerow(['service_type', 'destination_type', 'time_of_day',
                'event_count', 'volume', 'unit', 'unit_rate', 'amount',
                'currency', 'description'])
    for line in invoice.lines.all():
        w.writerow([
            line.service_type, line.destination_type, line.time_of_day,
            line.event_count, line.volume, line.unit, line.unit_rate,
            line.amount, line.currency, line.description,
        ])
    return buf.getvalue().encode('utf-8')
