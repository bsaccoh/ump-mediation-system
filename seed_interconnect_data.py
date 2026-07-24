import os
import sys
import django
from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone

sys.path.append(r'c:\Users\Saccoh1629182\Documents\Babah\BS\OCS\project\babah\ump-mediation-system')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import transaction
from interconnect.models import (
    InterconnectPartner, InterconnectRate, ExchangeRate,
    BillingCycle, Invoice, InvoiceLine, ReconciliationRecord, Settlement
)
from core.models import User

def seed_interconnect():
    print("Starting Interconnect Billing Sample Data Seeding...")
    admin_user = User.objects.filter(is_superuser=True).first()
    
    with transaction.atomic():
        # Get partners
        partners = {p.code: p for p in InterconnectPartner.objects.all()}
        
        if 'AFRIC' not in partners:
            print("Running seed_partners first would be good, but we will get/create them here just in case.")
            # Seed a default set if not found
            africell, _ = InterconnectPartner.objects.get_or_create(
                code='AFRIC',
                defaults={'name': 'Africell SL', 'country': 'Sierra Leone', 'is_local': True, 'default_currency': 'SLE'}
            )
            partners['AFRIC'] = africell
            
        africell = partners.get('AFRIC')
        qcell = partners.get('QCELL')
        vodauk = partners.get('VODAUK')
        mtnng = partners.get('MTNNG')
        
        # 1. Create rich Rates for AFRIC, QCELL, and VODAUK if they don't exist
        print("Seeding Interconnect Rates...")
        
        rates_to_create = [
            # Africell Inbound (we charge them for incoming traffic)
            {
                'partner': africell, 'direction': 'INBOUND', 'service_type': 'VOICE',
                'destination_type': 'NATIONAL', 'unit': 'PER_MINUTE', 'rate': Decimal('0.35'),
                'effective_from': date(2026, 1, 1), 'notes': 'Standard National Voice Termination Rate'
            },
            {
                'partner': africell, 'direction': 'INBOUND', 'service_type': 'SMS',
                'destination_type': 'NATIONAL', 'unit': 'PER_SMS', 'rate': Decimal('0.05'),
                'effective_from': date(2026, 1, 1), 'notes': 'Standard National SMS Termination Rate'
            },
            # Africell Outbound (they charge us for outgoing traffic)
            {
                'partner': africell, 'direction': 'OUTBOUND', 'service_type': 'VOICE',
                'destination_type': 'NATIONAL', 'unit': 'PER_MINUTE', 'rate': Decimal('0.38'),
                'effective_from': date(2026, 1, 1), 'notes': 'Africell National Voice Termination Rate'
            },
            {
                'partner': africell, 'direction': 'OUTBOUND', 'service_type': 'SMS',
                'destination_type': 'NATIONAL', 'unit': 'PER_SMS', 'rate': Decimal('0.06'),
                'effective_from': date(2026, 1, 1), 'notes': 'Africell National SMS Termination Rate'
            },
            # Vodafone UK Outbound (we terminate traffic on their international network)
            {
                'partner': vodauk, 'direction': 'OUTBOUND', 'service_type': 'VOICE',
                'destination_type': 'INTERNATIONAL', 'unit': 'PER_MINUTE', 'rate': Decimal('0.15'), # in GBP/USD typically, but model currency handles default
                'effective_from': date(2026, 1, 1), 'currency': 'GBP', 'notes': 'UK Mobile Termination Rate'
            },
            {
                'partner': vodauk, 'direction': 'OUTBOUND', 'service_type': 'SMS',
                'destination_type': 'INTERNATIONAL', 'unit': 'PER_SMS', 'rate': Decimal('0.04'),
                'effective_from': date(2026, 1, 1), 'currency': 'GBP', 'notes': 'UK SMS Termination Rate'
            },
            # Vodafone UK Inbound
            {
                'partner': vodauk, 'direction': 'INBOUND', 'service_type': 'VOICE',
                'destination_type': 'INTERNATIONAL', 'unit': 'PER_MINUTE', 'rate': Decimal('0.22'),
                'effective_from': date(2026, 1, 1), 'currency': 'GBP', 'notes': 'Inbound UK voice calls to Orange SL'
            }
        ]
        
        for rdata in rates_to_create:
            if rdata['partner'] is None:
                continue
            InterconnectRate.objects.get_or_create(
                partner=rdata['partner'],
                direction=rdata['direction'],
                service_type=rdata['service_type'],
                destination_type=rdata['destination_type'],
                effective_from=rdata['effective_from'],
                defaults={
                    'unit': rdata['unit'],
                    'rate': rdata['rate'],
                    'currency': rdata.get('currency', 'SLE'),
                    'notes': rdata['notes'],
                    'is_active': True
                }
            )
            
        # 2. Seeding Billing Cycles
        print("Seeding Billing Cycles...")
        # We will seed two billing cycles: March 2026 (CLOSED) and April 2026 (INVOICED / SETTLED)
        
        cycle_march_afric = BillingCycle.objects.create(
            partner=africell,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            status=BillingCycle.Status.INVOICED,
            our_voice_minutes=Decimal('124500.50'),
            our_voice_calls=41500,
            our_sms=82000,
            our_data_mb=Decimal('0.00'),
            partner_voice_minutes=Decimal('125100.20'),
            partner_voice_calls=41700,
            partner_sms=82400,
            partner_data_mb=Decimal('0.00'),
            variance_pct=Decimal('0.48'),
            notes='March 2026 Interconnect Cycle'
        )
        
        cycle_april_afric = BillingCycle.objects.create(
            partner=africell,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            status=BillingCycle.Status.SETTLED,
            our_voice_minutes=Decimal('158400.00'),
            our_voice_calls=52800,
            our_sms=94500,
            partner_voice_minutes=Decimal('158900.00'),
            partner_voice_calls=53000,
            partner_sms=94800,
            variance_pct=Decimal('0.31'),
            notes='April 2026 Interconnect Cycle'
        )
        
        cycle_march_vodauk = BillingCycle.objects.create(
            partner=vodauk,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            status=BillingCycle.Status.INVOICED,
            our_voice_minutes=Decimal('8200.40'),
            our_voice_calls=2050,
            our_sms=1200,
            partner_voice_minutes=Decimal('8150.00'),
            partner_voice_calls=2030,
            partner_sms=1190,
            variance_pct=Decimal('-0.61'),
            notes='March 2026 Vodafone UK Cycle'
        )
        
        # 3. Seeding Invoices
        print("Seeding Invoices and Lines...")
        
        # Invoice 1: March Africell Inbound (We invoice them for traffic terminated on Orange)
        # 124,500.50 mins * 0.35 = 43,575.18 SLE
        # 82,000 SMS * 0.05 = 4,100.00 SLE
        sub_voice = Decimal('43575.18')
        sub_sms = Decimal('4100.00')
        tax = (sub_voice + sub_sms) * Decimal('0.15') # 15% GST
        total = sub_voice + sub_sms + tax
        
        inv1 = Invoice.objects.create(
            partner=africell,
            billing_cycle=cycle_march_afric,
            direction=Invoice.Direction.INBOUND,
            invoice_number='INV-2026-03-AFRIC-IN',
            subtotal_voice=sub_voice,
            subtotal_sms=sub_sms,
            discount=Decimal('0.00'),
            tax=tax,
            total=total,
            currency='SLE',
            fx_rate_to_local=Decimal('1.00'),
            total_local=total,
            status=Invoice.Status.ISSUED,
            issued_at=timezone.now() - timedelta(days=25),
            due_date=date(2026, 5, 20),
            notes='Inbound voice/SMS invoice issued to Africell',
            created_by=admin_user
        )
        
        # Invoice 1 Lines
        InvoiceLine.objects.create(
            invoice=inv1, service_type='VOICE', destination_type='NATIONAL',
            volume=Decimal('124500.50'), event_count=41500, unit='PER_MINUTE',
            unit_rate=Decimal('0.35'), amount=sub_voice, currency='SLE',
            description='National Incoming Call Termination'
        )
        InvoiceLine.objects.create(
            invoice=inv1, service_type='SMS', destination_type='NATIONAL',
            volume=Decimal('82000.00'), event_count=82000, unit='PER_SMS',
            unit_rate=Decimal('0.05'), amount=sub_sms, currency='SLE',
            description='National Incoming SMS Termination'
        )

        # Invoice 2: March Africell Outbound (They invoice us for traffic terminated on Africell)
        # 125,100.20 mins * 0.38 = 47,538.08 SLE
        # 82,400 SMS * 0.06 = 4,944.00 SLE
        sub_voice_out = Decimal('47538.08')
        sub_sms_out = Decimal('4944.00')
        tax_out = (sub_voice_out + sub_sms_out) * Decimal('0.15')
        total_out = sub_voice_out + sub_sms_out + tax_out
        
        inv2 = Invoice.objects.create(
            partner=africell,
            billing_cycle=cycle_march_afric,
            direction=Invoice.Direction.OUTBOUND,
            invoice_number='INV-2026-03-AFRIC-OUT',
            subtotal_voice=sub_voice_out,
            subtotal_sms=sub_sms_out,
            discount=Decimal('0.00'),
            tax=tax_out,
            total=total_out,
            currency='SLE',
            fx_rate_to_local=Decimal('1.00'),
            total_local=total_out,
            status=Invoice.Status.SENT,
            issued_at=timezone.now() - timedelta(days=24),
            due_date=date(2026, 5, 20),
            notes='Outbound invoice received from Africell',
            created_by=admin_user
        )
        
        InvoiceLine.objects.create(
            invoice=inv2, service_type='VOICE', destination_type='NATIONAL',
            volume=Decimal('125100.20'), event_count=41700, unit='PER_MINUTE',
            unit_rate=Decimal('0.38'), amount=sub_voice_out, currency='SLE',
            description='National Outgoing Call Termination'
        )
        InvoiceLine.objects.create(
            invoice=inv2, service_type='SMS', destination_type='NATIONAL',
            volume=Decimal('82400.00'), event_count=82400, unit='PER_SMS',
            unit_rate=Decimal('0.06'), amount=sub_sms_out, currency='SLE',
            description='National Outgoing SMS Termination'
        )

        # Invoice 3: April Africell Inbound (Fully Settled)
        # 158,400 mins * 0.35 = 55,440.00 SLE
        # 94,500 SMS * 0.05 = 4,725.00 SLE
        sub_voice_apr = Decimal('55440.00')
        sub_sms_apr = Decimal('4725.00')
        tax_apr = (sub_voice_apr + sub_sms_apr) * Decimal('0.15')
        total_apr = sub_voice_apr + sub_sms_apr + tax_apr
        
        inv3 = Invoice.objects.create(
            partner=africell,
            billing_cycle=cycle_april_afric,
            direction=Invoice.Direction.INBOUND,
            invoice_number='INV-2026-04-AFRIC-IN',
            subtotal_voice=sub_voice_apr,
            subtotal_sms=sub_sms_apr,
            discount=Decimal('0.00'),
            tax=tax_apr,
            total=total_apr,
            currency='SLE',
            fx_rate_to_local=Decimal('1.00'),
            total_local=total_apr,
            status=Invoice.Status.PAID,
            issued_at=timezone.now() - timedelta(days=5),
            due_date=date(2026, 5, 10),
            paid_at=timezone.now() - timedelta(days=2),
            notes='Paid inbound invoice',
            created_by=admin_user
        )
        
        InvoiceLine.objects.create(
            invoice=inv3, service_type='VOICE', destination_type='NATIONAL',
            volume=Decimal('158400.00'), event_count=52800, unit='PER_MINUTE',
            unit_rate=Decimal('0.35'), amount=sub_voice_apr, currency='SLE',
            description='National Incoming Call Termination'
        )
        
        # 4. Seeding Settlements
        print("Seeding Settlements...")
        Settlement.objects.create(
            invoice=inv3,
            amount=total_apr,
            currency='SLE',
            fx_rate_to_local=Decimal('1.00'),
            amount_local=total_apr,
            payment_date=date(2026, 5, 15),
            payment_method=Settlement.PaymentMethod.WIRE,
            payment_reference='TXN-AFRIC-202604',
            notes='Full bank wire payment from Africell for April period',
            recorded_by=admin_user
        )

        # 5. Seeding Reconciliation Records
        print("Seeding Reconciliation records...")
        
        # March Voice Reconciliation
        ReconciliationRecord.objects.create(
            partner=africell,
            billing_cycle=cycle_march_afric,
            service_type='VOICE',
            destination_type='NATIONAL',
            our_volume=Decimal('124500.50'),
            our_amount=sub_voice,
            partner_volume=Decimal('125100.20'),
            partner_amount=sub_voice_out,
            variance_volume=Decimal('-599.70'),
            variance_amount=Decimal('-3962.90'),
            variance_pct=Decimal('-0.48'),
            status=ReconciliationRecord.Status.MATCHED,
            partner_file_ref='AFRIC_MARCH_VOICE_v1.csv',
            resolution_notes='Matched well within the 1% SLA tolerance.'
        )

        # March SMS Reconciliation
        ReconciliationRecord.objects.create(
            partner=africell,
            billing_cycle=cycle_march_afric,
            service_type='SMS',
            destination_type='NATIONAL',
            our_volume=Decimal('82000.00'),
            our_amount=sub_sms,
            partner_volume=Decimal('82400.00'),
            partner_amount=sub_sms_out,
            variance_volume=Decimal('-400.00'),
            variance_amount=Decimal('-844.00'),
            variance_pct=Decimal('-0.49'),
            status=ReconciliationRecord.Status.MATCHED,
            partner_file_ref='AFRIC_MARCH_SMS_v1.csv',
            resolution_notes='Matched well within the 1% SLA tolerance.'
        )

        # April Voice Reconciliation
        ReconciliationRecord.objects.create(
            partner=africell,
            billing_cycle=cycle_april_afric,
            service_type='VOICE',
            destination_type='NATIONAL',
            our_volume=Decimal('158400.00'),
            our_amount=sub_voice_apr,
            partner_volume=Decimal('158900.00'),
            partner_amount=Decimal('60382.00'),
            variance_volume=Decimal('-500.00'),
            variance_amount=Decimal('-4942.00'),
            variance_pct=Decimal('-0.31'),
            status=ReconciliationRecord.Status.RESOLVED,
            partner_file_ref='AFRIC_APRIL_VOICE.csv',
            resolution_notes='Discrepancy resolved. Offset agreement approved.',
            resolved_at=timezone.now() - timedelta(days=2),
            resolved_by=admin_user
        )

    print("Interconnect Billing Sample Data Seeding Complete!")

if __name__ == '__main__':
    seed_interconnect()
