"""Seed regulatory configuration: tax types/rates, tariffs, and risk rules.

Usage:
    python manage.py seed_regulatory          # seed all
    python manage.py seed_regulatory --only tax
    python manage.py seed_regulatory --only tariffs
    python manage.py seed_regulatory --only risk
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction


EFFECTIVE_FROM = date(2026, 1, 1)

OPERATORS = ['orange', 'africell', 'qcell', 'sierratel']


class Command(BaseCommand):
    help = 'Seed tax types/rates, tariffs, and risk rules for all operators'

    def add_arguments(self, parser):
        parser.add_argument(
            '--only', choices=['tax', 'tariffs', 'risk', 'all'], default='all',
            help='Seed only a specific category',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        only = options['only']

        if only in ('all', 'tax'):
            self._seed_tax()
        if only in ('all', 'tariffs'):
            self._seed_tariffs()
        if only in ('all', 'risk'):
            self._seed_risk_rules()

        self.stdout.write(self.style.SUCCESS('Regulatory seed complete.'))

    def _seed_tax(self):
        from regulatory.models.tax import TaxType, TaxRate

        gst, created = TaxType.objects.update_or_create(
            code='gst-telecom',
            defaults={
                'name': 'Goods & Services Tax (Telecom)',
                'category': TaxType.Category.GST,
                'description': '15% GST on telecom services (voice, SMS, data) — NRA',
                'applicable_services': ['VOICE', 'SMS', 'DATA'],
                'applicable_operators': [],
                'is_active': True,
            },
        )
        tag = 'created' if created else 'updated'
        self.stdout.write(f'  TaxType: {gst.name} [{tag}]')

        rate, created = TaxRate.objects.update_or_create(
            tax_type=gst,
            version=1,
            defaults={
                'rate_percent': Decimal('15.000'),
                'effective_from': EFFECTIVE_FROM,
                'effective_to': None,
                'status': TaxRate.Status.ACTIVE,
                'treatment': TaxRate.Treatment.EXCLUSIVE,
                'notes': 'Standard GST rate for all telecom services',
            },
        )
        tag = 'created' if created else 'updated'
        self.stdout.write(f'  TaxRate: {rate} [{tag}]')

    def _seed_tariffs(self):
        from regulatory.models.tariffs import Tariff

        tariff_defs = []
        for op in OPERATORS:
            tariff_defs.extend([
                {
                    'name': f'{op.title()} Voice Local On-Net',
                    'operator_code': op,
                    'service_type': 'VOICE',
                    'traffic_type': 'ON_NET',
                    'rate': Decimal('1.81'),
                    'charging_unit': 'PER_MINUTE',
                    'minimum_charge': Decimal('1.81'),
                },
                {
                    'name': f'{op.title()} Voice Local Off-Net',
                    'operator_code': op,
                    'service_type': 'VOICE',
                    'traffic_type': 'OFF_NET',
                    'rate': Decimal('1.81'),
                    'charging_unit': 'PER_MINUTE',
                    'minimum_charge': Decimal('1.81'),
                },
                {
                    'name': f'{op.title()} SMS Local On-Net',
                    'operator_code': op,
                    'service_type': 'SMS',
                    'traffic_type': 'ON_NET',
                    'rate': Decimal('0.25'),
                    'charging_unit': 'PER_MESSAGE',
                    'minimum_charge': Decimal('0.25'),
                },
                {
                    'name': f'{op.title()} SMS Local Off-Net',
                    'operator_code': op,
                    'service_type': 'SMS',
                    'traffic_type': 'OFF_NET',
                    'rate': Decimal('0.90'),
                    'charging_unit': 'PER_MESSAGE',
                    'minimum_charge': Decimal('0.90'),
                },
                {
                    'name': f'{op.title()} Data Usage',
                    'operator_code': op,
                    'service_type': 'DATA',
                    'traffic_type': 'LOCAL',
                    'rate': Decimal('20.00'),
                    'charging_unit': 'PER_GB',
                    'minimum_charge': Decimal('0.00'),
                },
            ])

        created_count = 0
        updated_count = 0
        for t in tariff_defs:
            obj, created = Tariff.objects.update_or_create(
                operator_code=t['operator_code'],
                service_type=t['service_type'],
                traffic_type=t['traffic_type'],
                version=1,
                defaults={
                    'name': t['name'],
                    'rate': t['rate'],
                    'charging_unit': t['charging_unit'],
                    'minimum_charge': t['minimum_charge'],
                    'subscriber_type': 'ALL',
                    'rounding_rule': 'CEIL',
                    'currency': 'SLE',
                    'tax_treatment': 'TAXABLE',
                    'effective_from': EFFECTIVE_FROM,
                    'effective_to': None,
                    'status': Tariff.Status.ACTIVE,
                    'is_regulatory_reference': True,
                    'notes': 'Seeded by seed_regulatory command',
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(f'  Tariffs: {created_count} created, {updated_count} updated')

    def _seed_risk_rules(self):
        from regulatory.models.risk import RiskRule

        rules = [
            {
                'name': 'Revenue Drop > 20%',
                'code': 'REV-DROP-20',
                'alert_type': 'REVENUE',
                'rule_type': 'THRESHOLD',
                'metric': 'revenue_variance_pct',
                'comparison_operator': 'GT',
                'threshold_value': Decimal('20.00'),
                'threshold_unit': 'PERCENTAGE',
                'severity': 'HIGH',
                'description': 'Triggers when operator revenue drops more than 20% compared to previous period',
                'lookback_period': 30,
                'auto_create_alert': True,
                'auto_escalate': False,
            },
            {
                'name': 'Traffic Spike > 50%',
                'code': 'TRF-SPIKE-50',
                'alert_type': 'TRAFFIC',
                'rule_type': 'THRESHOLD',
                'metric': 'traffic_spike_pct',
                'comparison_operator': 'GT',
                'threshold_value': Decimal('50.00'),
                'threshold_unit': 'PERCENTAGE',
                'severity': 'MEDIUM',
                'description': 'Triggers when traffic volume increases more than 50% compared to baseline',
                'lookback_period': 7,
                'auto_create_alert': True,
                'auto_escalate': False,
            },
            {
                'name': 'Zero Revenue Hour Detected',
                'code': 'REV-ZERO-HR',
                'alert_type': 'REVENUE',
                'rule_type': 'THRESHOLD',
                'metric': 'zero_revenue_hours',
                'comparison_operator': 'GTE',
                'threshold_value': Decimal('1.00'),
                'threshold_unit': 'COUNT',
                'severity': 'HIGH',
                'description': 'Triggers when an operator reports zero revenue for one or more hours during business hours',
                'lookback_period': 1,
                'auto_create_alert': True,
                'auto_escalate': True,
                'escalation_threshold': 3,
            },
            {
                'name': 'GST Variance > 10%',
                'code': 'GST-VAR-10',
                'alert_type': 'GST',
                'rule_type': 'THRESHOLD',
                'metric': 'gst_variance_pct',
                'comparison_operator': 'GT',
                'threshold_value': Decimal('10.00'),
                'threshold_unit': 'PERCENTAGE',
                'severity': 'HIGH',
                'description': 'Triggers when declared GST differs from expected GST by more than 10%',
                'lookback_period': 30,
                'auto_create_alert': True,
                'auto_escalate': True,
                'escalation_threshold': 2,
            },
            {
                'name': 'CDR Gap > 1 Hour',
                'code': 'DQ-GAP-1H',
                'alert_type': 'DATA_QUALITY',
                'rule_type': 'THRESHOLD',
                'metric': 'cdr_gap_minutes',
                'comparison_operator': 'GT',
                'threshold_value': Decimal('60.00'),
                'threshold_unit': 'DURATION',
                'severity': 'MEDIUM',
                'description': 'Triggers when there is a gap of more than 1 hour in CDR data from an operator',
                'lookback_period': 1,
                'auto_create_alert': True,
                'auto_escalate': False,
            },
        ]

        created_count = 0
        updated_count = 0
        for r in rules:
            obj, created = RiskRule.objects.update_or_create(
                code=r['code'],
                defaults={
                    'name': r['name'],
                    'alert_type': r['alert_type'],
                    'rule_type': r['rule_type'],
                    'metric': r['metric'],
                    'comparison_operator': r['comparison_operator'],
                    'threshold_value': r['threshold_value'],
                    'threshold_unit': r['threshold_unit'],
                    'severity': r['severity'],
                    'description': r['description'],
                    'lookback_period': r.get('lookback_period'),
                    'auto_create_alert': r.get('auto_create_alert', True),
                    'auto_escalate': r.get('auto_escalate', False),
                    'escalation_threshold': r.get('escalation_threshold'),
                    'operator_code': '',
                    'service_scope': '',
                    'status': RiskRule.Status.ACTIVE,
                    'effective_from': EFFECTIVE_FROM,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(f'  Risk rules: {created_count} created, {updated_count} updated')
