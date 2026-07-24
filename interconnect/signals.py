"""Interconnect signals.

Settlement post_save → update Invoice.status when cumulative payments
fully cover the invoice total.
"""
from decimal import Decimal

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone

from .models import Settlement, Invoice


def _recompute_invoice_status(invoice: Invoice) -> None:
    paid = invoice.amount_paid
    if paid <= Decimal('0'):
        # Revert to ISSUED/DRAFT if previously marked PAID/PART_PAID
        if invoice.status in (Invoice.Status.PAID, Invoice.Status.PART_PAID):
            invoice.status = Invoice.Status.ISSUED
            invoice.paid_at = None
            invoice.save(update_fields=['status', 'paid_at', 'updated_at'])
        return

    if paid >= invoice.total and invoice.total > Decimal('0'):
        invoice.status = Invoice.Status.PAID
        invoice.paid_at = invoice.paid_at or timezone.now()
        invoice.save(update_fields=['status', 'paid_at', 'updated_at'])
    else:
        invoice.status = Invoice.Status.PART_PAID
        invoice.save(update_fields=['status', 'updated_at'])


@receiver(post_save, sender=Settlement)
def settlement_saved(sender, instance: Settlement, **kwargs):
    _recompute_invoice_status(instance.invoice)


@receiver(post_delete, sender=Settlement)
def settlement_deleted(sender, instance: Settlement, **kwargs):
    try:
        _recompute_invoice_status(instance.invoice)
    except Invoice.DoesNotExist:
        pass
