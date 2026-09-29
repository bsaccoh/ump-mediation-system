"""
Tier 1 — golden summary. Runs the full ingestion pipeline and fingerprints what
actually landed in the database.

The per-field NULL COUNTS are the point of this file. Asserting means alone
cannot catch silent field loss: the mean of an all-null column is simply absent,
so a field that stops being persisted looks identical to a field that was never
in the source. Null counts make that impossible to miss — they are what would
have caught ss_rsrp/ss_rsrq/ss_sinr being dropped in _flush_chunk.
"""
from __future__ import annotations

import hashlib

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Max, Min
from django.test import TestCase, override_settings
from django.utils import timezone

from drive_test.models import (
    DriveTestFile, DriveTestSession, HandoverEvent, Measurement,
    ParserProfile, RadioMeasurement, ServiceMeasurement,
)
from reference.models import Operator

from . import compare_or_write, corpus_files

#: RadioMeasurement columns tracked for presence. Adding a column here without
#: regenerating snapshots is a deliberate, reviewable diff.
_RADIO_FIELDS = [
    'technology', 'rssi', 'rscp', 'ecio', 'rsrp', 'rsrq', 'sinr', 'cqi',
    'ss_rsrp', 'ss_rsrq', 'ss_sinr', 'dl_throughput_kbps', 'ul_throughput_kbps',
]

#: Numeric columns summarised as min/max/mean.
_RADIO_NUMERIC = [
    'rssi', 'rscp', 'ecio', 'rsrp', 'rsrq', 'sinr', 'cqi',
    'ss_rsrp', 'ss_rsrq', 'ss_sinr', 'dl_throughput_kbps', 'ul_throughput_kbps',
]


def _round(value):
    return None if value is None else round(float(value), 2)


def _histogram(queryset, field):
    rows = queryset.values(field).annotate(n=Count('*')).order_by(field)
    return {str(r[field] if r[field] not in (None, '') else '<none>'): r['n'] for r in rows}


@override_settings(UMP_STORAGE_ROOT=None)
class GoldenSummaryTests(TestCase):
    """Ingest each corpus file and compare the resulting database state."""

    @classmethod
    def setUpTestData(cls):
        cls.operator = Operator.objects.create(code='orange', name='Orange', enabled=True)
        cls.user = get_user_model().objects.create_user(
            username='regression', password='x', is_staff=True,
        )
        # Seeded by migration 0002. Its absence is itself a regression.
        cls.profile = ParserProfile.objects.filter(
            parser_class__endswith='TrpDriveTestParser'
        ).first()

    def test_pipeline_output_is_stable(self):
        files = corpus_files()
        if not files:
            self.skipTest('TRP corpus not present (data/drive-test/raw/*/trp/)')
        self.assertIsNotNone(
            self.profile,
            'TRP ParserProfile missing — migration 0002 did not seed it',
        )

        for source in files:
            with self.subTest(file=source.name):
                self._check(source)

    def _check(self, source):
        from core.models import JobRecord
        from drive_test.tasks import _run_process_drive_test_file

        session = DriveTestSession.objects.create(
            operator=self.operator,
            test_date=timezone.now().date(),
            uploaded_by=self.user,
            status='PROCESSING',
        )
        drive_file = DriveTestFile.objects.create(
            session=session,
            original_filename=source.name,
            file_path=str(source),
            file_size=source.stat().st_size,
            # Dedup is exercised in Tier 4; a deterministic stand-in avoids
            # hashing tens of megabytes here.
            sha256=hashlib.sha256(source.name.encode()).hexdigest(),
            parser_profile=self.profile,
            detected_format=self.profile.name,
        )

        # @tracked_task rewrites the signature to (job_id, *args) for JobRecord
        # bookkeeping, so drive the pipeline exactly as production does rather
        # than reaching past the decorator.
        job = JobRecord.objects.create(
            job_type='drive_test.process_file',
            label=f'Regression {source.name}'[:200],
            status=JobRecord.Status.PENDING,
        )
        _run_process_drive_test_file(job.pk, drive_file.pk)

        measurements = Measurement.objects.filter(drive_file=drive_file)
        radio = RadioMeasurement.objects.filter(measurement__drive_file=drive_file)
        services = ServiceMeasurement.objects.filter(measurement__drive_file=drive_file)
        handovers = HandoverEvent.objects.filter(measurement__drive_file=drive_file)

        total = measurements.count()

        # Presence per radio column. Count(field) ignores NULLs, which is
        # precisely the signal we want.
        radio_present = radio.aggregate(
            **{f: Count(f) for f in _RADIO_FIELDS}
        )
        radio_total = radio.count()

        stats = radio.aggregate(**{
            f'{f}__{agg_name}': agg(f)
            for f in _RADIO_NUMERIC
            for agg_name, agg in (('min', Min), ('max', Max), ('mean', Avg))
        })

        bounds = measurements.aggregate(
            lat_min=Min('latitude'), lat_max=Max('latitude'),
            lon_min=Min('longitude'), lon_max=Max('longitude'),
            first=Min('captured_at'), last=Max('captured_at'),
        )

        quality_flags: dict[str, int] = {}
        for flags in measurements.values_list('quality_flags', flat=True):
            for flag in (flags or []):
                quality_flags[flag] = quality_flags.get(flag, 0) + 1

        actual = {
            'file_status': DriveTestFile.objects.get(pk=drive_file.pk).status,
            'measurement_count': total,
            'measurement_count_on_file': DriveTestFile.objects.get(pk=drive_file.pk).measurement_count,
            'valid_count': measurements.filter(is_valid=True).count(),
            'invalid_count': measurements.filter(is_valid=False).count(),
            'with_coordinates': measurements.exclude(latitude=0, longitude=0).count(),

            'matched_count': measurements.filter(matched_cell__isnull=False).count(),
            'match_methods': _histogram(measurements, 'match_method'),

            'radio_row_count': radio_total,
            'radio_present': {f: radio_present[f] for f in _RADIO_FIELDS},
            'radio_absent': {f: radio_total - radio_present[f] for f in _RADIO_FIELDS},
            'radio_stats': {k: _round(v) for k, v in sorted(stats.items())},
            'technologies': _histogram(radio, 'technology'),

            'service_count': services.count(),
            'service_types': _histogram(services, 'service_type'),
            'service_outcomes': _histogram(services, 'outcome'),

            'handover_count': handovers.count(),
            'handover_types': _histogram(handovers, 'ho_type'),
            'handover_results': _histogram(handovers, 'result'),

            'distinct_gsm_cells': measurements.values('obs_mcc', 'obs_mnc', 'obs_lac', 'obs_ci').distinct().count(),
            'distinct_lte_cells': measurements.values('obs_tac', 'obs_eci').distinct().count(),
            'distinct_pci': measurements.exclude(obs_pci=None).values('obs_pci').distinct().count(),

            'latitude_min': _round(bounds['lat_min']),
            'latitude_max': _round(bounds['lat_max']),
            'longitude_min': _round(bounds['lon_min']),
            'longitude_max': _round(bounds['lon_max']),
            'first_captured_at': bounds['first'].isoformat() if bounds['first'] else None,
            'last_captured_at': bounds['last'].isoformat() if bounds['last'] else None,

            'quality_flags': dict(sorted(quality_flags.items())),
        }

        compare_or_write(self, actual, source, 'golden')
