"""Seed Operators + SourcePatterns for multi-operator/vendor mediation.

Idempotent: safe to re-run. Creates the Sierra Leone operators and example
filename->(operator/vendor/network element) patterns. Operators adjust the
patterns to their own naming conventions afterwards.

    python manage.py seed_operators
"""
from django.core.management.base import BaseCommand

from core.enums import DecoderType
from reference.models import Operator, SourcePattern

OPERATORS = [
    # code, name, plmn, mcc, mnc
    ('orange',    'Orange Sierra Leone',   '61901', '619', '01'),
    ('africell',  'Africell Sierra Leone', '61902', '619', '02'),
    ('qcell',     'Qcell Sierra Leone',    '61903', '619', '03'),
    ('sierratel', 'Sierra Tel',            '61904', '619', '04'),
    ('onemobile', 'One-Mobile',            '61905', '619', '05'),
]

# operator_code, pattern, vendor, network_element, decoder_type, priority
# Patterns match the network-element token in the filename, independent of the
# source prefix — Orange's feeds use both bFT* (MSC/IMS/SGSN) and dc1* (PGW/SGW),
# e.g. bFTMSX01..., bFTATS01..., bFTSGSN..., dc1PGWCDR..., dc1SGWCDR...
PATTERNS = [
    ('orange', 'msx',     'huawei', 'msc',  DecoderType.MSC,  10),  # bFTMSX...
    ('orange', 'ats',     'huawei', 'ims',  DecoderType.IMS,  10),  # bFTATS...
    ('orange', 'pgwcdr',  'huawei', 'pgw',  DecoderType.PGW,  10),  # bFTPGWCDR / dc1PGWCDR
    ('orange', 'sgwcdr',  'huawei', 'sgw',  DecoderType.SGW,  10),  # bFTSGWCDR / dc1SGWCDR
    ('orange', 'sgsn',    'huawei', 'sgsn', DecoderType.SGSN, 10),  # bFTSGSN / dc1SGSN
    ('orange', 'cbs_cdr', 'huawei', 'cbs',  DecoderType.CBS,  10),
]


class Command(BaseCommand):
    help = 'Seed Operators and example SourcePatterns (idempotent).'

    def handle(self, *args, **opts):
        ops = {}
        for code, name, plmn, mcc, mnc in OPERATORS:
            op, created = Operator.objects.update_or_create(
                code=code,
                defaults=dict(name=name, home_plmn=plmn, home_mcc=mcc,
                              home_mnc=mnc, country_code='232', enabled=True),
            )
            ops[code] = op
            self.stdout.write(f"{'created' if created else 'updated'} operator {code}")

        # Reset patterns for the seeded operators so changed patterns don't
        # leave stale rows behind (idempotent full refresh).
        SourcePattern.objects.filter(operator__in=ops.values()).delete()
        for code, pattern, vendor, ne, decoder, prio in PATTERNS:
            SourcePattern.objects.create(
                pattern=pattern, operator=ops[code],
                vendor=vendor, network_element=ne,
                decoder_type=decoder, priority=prio,
                is_regex=False, enabled=True,
            )
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(ops)} operators and {len(PATTERNS)} source patterns.'))
