"""Seed comprehensive regulatory demo data so every page shows content.

Usage::

    python manage.py seed_regulatory_demo          # additive
    python manage.py seed_regulatory_demo --reset  # wipe regulatory rows first

What gets seeded
----------------
* 3 RetailRevenue entries (last 3 months, with realistic SLE values)
* 1 LeviedPeriod computed for the most-recent completed month
* 2 LEARequest rows: one OPEN, one FULFILLED with a matching extraction
* 1 RegulatoryReport for each of the 4 types covering last month
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from regulatory.models import (
    RegulatoryProfile, RetailRevenue, RegulatoryReport,
    LeviedPeriod, LEARequest, LEAExtractionLog,
)


def _last_completed_month(today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def _month_range(y: int, m: int) -> tuple[date, date]:
    start = date(y, m, 1)
    end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    return start, end


class Command(BaseCommand):
    help = 'Seed comprehensive regulatory demo data.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Delete existing regulatory rows first.')

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts['reset']:
            self._reset()

        retail = self._seed_retail()
        levy = self._seed_levy()
        lea = self._seed_lea()
        reports = self._seed_reports()

        self.stdout.write(self.style.SUCCESS(
            f'\nSeed complete:\n'
            f'  RetailRevenue   +{retail}  (total {RetailRevenue.objects.count()})\n'
            f'  LeviedPeriod    +{levy}    (total {LeviedPeriod.objects.count()})\n'
            f'  LEARequest      +{lea}     (total {LEARequest.objects.count()})\n'
            f'  RegulatoryReport+{reports} (total {RegulatoryReport.objects.count()})\n'
        ))

    def _reset(self):
        LEAExtractionLog.objects.all().delete()
        LEARequest.objects.all().delete()
        RegulatoryReport.objects.all().delete()
        LeviedPeriod.objects.all().delete()
        RetailRevenue.objects.all().delete()
        self.stdout.write('Reset: cleared retail / levy / LEA / reports.')

    def _seed_retail(self) -> int:
        added = 0
        # Last 3 completed months
        y, m = _last_completed_month()
        months = []
        for _ in range(3):
            months.append((y, m))
            if m == 1:
                y -= 1; m = 12
            else:
                m -= 1
        random.seed(42)
        for yr, mn in reversed(months):
            voice = Decimal(str(random.randint(80_000, 150_000)))
            sms = Decimal(str(random.randint(15_000, 30_000)))
            data = Decimal(str(random.randint(40_000, 90_000)))
            other = Decimal(str(random.randint(3_000, 12_000)))
            _, created = RetailRevenue.objects.update_or_create(
                period_year=yr, period_month=mn,
                defaults=dict(
                    voice_revenue=voice, sms_revenue=sms,
                    data_revenue=data, other_revenue=other,
                    currency='SLE',
                    notes=f'Demo retail figures for {yr}-{mn:02d}',
                ),
            )
            if created:
                added += 1
        return added

    def _seed_levy(self) -> int:
        from regulatory.engines.levy import compute_levy
        y, m = _last_completed_month()
        before = LeviedPeriod.objects.count()
        try:
            compute_levy(y, m)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Levy compute failed: {e}'))
        return LeviedPeriod.objects.count() - before

    def _seed_lea(self) -> int:
        added = 0
        now = timezone.now()
        scope_end = now
        scope_start = scope_end - timedelta(days=7)

        # 1) OPEN request — no extraction yet
        _, created = LEARequest.objects.update_or_create(
            case_number='CR-2026-001',
            defaults=dict(
                requesting_agency='Sierra Leone Police',
                officer_name='Insp. Bangura',
                officer_contact='+232 76 111 111',
                legal_basis='Court order HC/2026/SOL/045',
                filter_msisdn='23276576003',
                filter_start=scope_start,
                filter_end=scope_end,
                status=LEARequest.Status.OPEN,
                notes='Suspect in fraud investigation — initial CDR sweep.',
            ),
        )
        if created:
            added += 1

        # 2) FULFILLED request with extraction record
        req2, created = LEARequest.objects.update_or_create(
            case_number='CR-2026-002',
            defaults=dict(
                requesting_agency='Anti-Corruption Commission',
                officer_name='Mr Sesay',
                officer_contact='+232 76 222 222',
                legal_basis='Subpoena ACC/2026/INV/012',
                filter_msisdn='23277123456',
                filter_start=scope_start,
                filter_end=scope_end,
                status=LEARequest.Status.FULFILLED,
                fulfilled_at=now,
                notes='Sample fulfilled request with extraction record.',
            ),
        )
        if created:
            added += 1
            # Stub extraction record (no real CDRs likely for this MSISDN)
            LEAExtractionLog.objects.create(
                request=req2,
                record_count=0,
                sha256='demo' + ('0' * 60),
                criteria_json={
                    'msisdn': req2.filter_msisdn,
                    'start': req2.filter_start.isoformat(),
                    'end': req2.filter_end.isoformat(),
                },
                notes='Demo seed — empty extraction.',
            )
        return added

    def _seed_reports(self) -> int:
        from regulatory.engines.reports import generate_report
        y, m = _last_completed_month()
        start, end = _month_range(y, m)
        added = 0
        for rtype in ('TRAFFIC', 'REVENUE', 'SUBSCRIBER', 'INTERCONNECT_SUMMARY'):
            if RegulatoryReport.objects.filter(
                report_type=rtype, period_start=start, period_end=end,
            ).exists():
                continue
            try:
                generate_report(rtype, start, end)
                added += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'{rtype} report failed: {e}'))
        return added
