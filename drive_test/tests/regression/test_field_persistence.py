"""
Tier 4 — field persistence, idempotency and parser resolution.

These tests use synthetic fixtures rather than the TRP corpus, because the
corpus cannot cover them:

  * The TRP files contain no 5G, so nothing in it would notice ss_rsrp,
    ss_rsrq and ss_sinr being dropped on insert — which is exactly what was
    happening. This fixture is mandatory, not optional.
  * SINR 0 dB and CQI 0 are valid readings that a truthiness guard silently
    treats as absent.

They run without the corpus, so they execute in CI as well as locally.
"""
from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from drive_test.models import (
    DriveTestFile, DriveTestSession, Measurement, ParserProfile, RadioMeasurement,
)
from drive_test.parsers.base import ParsedMeasurement
from reference.models import Operator


def _parsed(seq: int, **overrides) -> ParsedMeasurement:
    """A minimally valid ParsedMeasurement with optional field overrides."""
    base = dict(
        sequence_num=seq,
        captured_at=datetime(2026, 1, 1, 12, 0, seq % 60),
        latitude=8.4840,
        longitude=-13.2299,
    )
    base.update(overrides)
    return ParsedMeasurement(**base)


class _PipelineFixture(TestCase):
    """Shared setup: an operator, a user, a session and a DriveTestFile row."""

    @classmethod
    def setUpTestData(cls):
        cls.operator = Operator.objects.create(code='orange', name='Orange', enabled=True)
        cls.user = get_user_model().objects.create_user(username='fixture', password='x')
        cls.profile = ParserProfile.objects.filter(
            parser_class__endswith='TrpDriveTestParser'
        ).first()

    def setUp(self):
        self.session = DriveTestSession.objects.create(
            operator=self.operator,
            test_date=timezone.now().date(),
            uploaded_by=self.user,
            status='PROCESSING',
        )
        self.drive_file = DriveTestFile.objects.create(
            session=self.session,
            original_filename='synthetic.trp',
            file_path='/nonexistent/synthetic.trp',
            file_size=1,
            sha256=hashlib.sha256(str(self.id()).encode()).hexdigest(),
            parser_profile=self.profile,
        )

    def _flush(self, parsed: list[ParsedMeasurement]) -> int:
        """Run _flush_chunk over the given ParsedMeasurement objects."""
        from drive_test.tasks import _flush_chunk

        chunk = [
            (
                Measurement(
                    drive_file=self.drive_file,
                    sequence_num=pm.sequence_num,
                    captured_at=pm.captured_at,
                    latitude=pm.latitude,
                    longitude=pm.longitude,
                    is_valid=pm.is_valid,
                    quality_flags=pm.quality_flags,
                ),
                pm,
            )
            for pm in parsed
        ]
        return _flush_chunk(chunk, [], [], self.drive_file)


class FiveGFieldPersistenceTests(_PipelineFixture):
    """The 5G columns must survive _flush_chunk.

    Regression guard for the defect where ss_rsrp/ss_rsrq/ss_sinr existed on
    both ParsedMeasurement and RadioMeasurement but were never passed to the
    model constructor, making all 5G capture a silent no-op.
    """

    def test_nr_fields_are_persisted(self):
        self._flush([
            _parsed(1, technology='5G', ss_rsrp=-88.5, ss_rsrq=-11.25, ss_sinr=14.75),
        ])

        radio = RadioMeasurement.objects.get(measurement__drive_file=self.drive_file)
        self.assertEqual(radio.technology, '5G')
        self.assertAlmostEqual(radio.ss_rsrp, -88.5, places=4)
        self.assertAlmostEqual(radio.ss_rsrq, -11.25, places=4)
        self.assertAlmostEqual(radio.ss_sinr, 14.75, places=4)

    def test_nr_only_sample_creates_a_radio_row(self):
        """A pure-5G sample with no technology string must still persist.

        The old guard inspected only the LTE/UMTS/GSM columns, so a sample
        carrying nothing but SS measurements produced no RadioMeasurement.
        """
        self._flush([_parsed(1, ss_rsrp=-95.0)])

        radio = RadioMeasurement.objects.filter(measurement__drive_file=self.drive_file)
        self.assertEqual(radio.count(), 1)
        self.assertAlmostEqual(radio.first().ss_rsrp, -95.0, places=4)

    def test_lte_fields_still_persist(self):
        """Guard against fixing 5G by breaking 4G."""
        self._flush([
            _parsed(1, technology='4G', rsrp=-102.0, rsrq=-13.0, sinr=5.5, cqi=9),
        ])

        radio = RadioMeasurement.objects.get(measurement__drive_file=self.drive_file)
        self.assertAlmostEqual(radio.rsrp, -102.0, places=4)
        self.assertAlmostEqual(radio.rsrq, -13.0, places=4)
        self.assertAlmostEqual(radio.sinr, 5.5, places=4)
        self.assertEqual(radio.cqi, 9)


class ZeroValuedReadingTests(_PipelineFixture):
    """Zero is a reading, not a missing value."""

    def test_zero_sinr_is_persisted(self):
        """SINR 0 dB is entirely realistic and must not be treated as absent."""
        self._flush([_parsed(1, sinr=0.0)])

        radio = RadioMeasurement.objects.filter(measurement__drive_file=self.drive_file)
        self.assertEqual(radio.count(), 1, 'SINR 0 dB was discarded as falsy')
        self.assertEqual(radio.first().sinr, 0.0)

    def test_zero_cqi_is_persisted(self):
        self._flush([_parsed(1, cqi=0)])

        radio = RadioMeasurement.objects.filter(measurement__drive_file=self.drive_file)
        self.assertEqual(radio.count(), 1, 'CQI 0 was discarded as falsy')
        self.assertEqual(radio.first().cqi, 0)

    def test_zero_throughput_is_persisted(self):
        """Zero throughput is a meaningful observation — a stalled transfer."""
        self._flush([_parsed(1, dl_throughput_kbps=0.0)])

        radio = RadioMeasurement.objects.filter(measurement__drive_file=self.drive_file)
        self.assertEqual(radio.count(), 1, 'zero throughput was discarded as falsy')

    def test_sample_with_no_radio_data_creates_no_radio_row(self):
        """The guard must still exclude genuinely empty samples."""
        self._flush([_parsed(1)])

        self.assertEqual(
            RadioMeasurement.objects.filter(measurement__drive_file=self.drive_file).count(),
            0,
        )
        self.assertEqual(
            Measurement.objects.filter(drive_file=self.drive_file).count(),
            1,
            'the measurement itself should still be stored',
        )


class ParserResolutionTests(_PipelineFixture):
    """_resolve_parser must fail loudly rather than guessing.

    It previously fell back to the CSV parser whenever a profile was missing or
    unloadable, which silently produced garbage measurements from binary files.
    """

    def test_missing_profile_raises(self):
        from drive_test.tasks import _resolve_parser

        self.drive_file.parser_profile = None
        with self.assertRaises(ValueError) as ctx:
            _resolve_parser(self.drive_file)
        self.assertIn('synthetic.trp', str(ctx.exception))

    def test_blank_parser_class_raises(self):
        from drive_test.tasks import _resolve_parser

        profile = ParserProfile.objects.create(
            name='Broken (no class)', parser_class='', vendor='',
            file_extensions=['.xyz'], magic_bytes='', header_signature='',
            default_config={}, is_active=True,
        )
        self.drive_file.parser_profile = profile
        with self.assertRaises(ValueError) as ctx:
            _resolve_parser(self.drive_file)
        self.assertIn('parser_class', str(ctx.exception))

    def test_unimportable_parser_class_raises(self):
        from drive_test.tasks import _resolve_parser

        profile = ParserProfile.objects.create(
            name='Broken (bad path)',
            parser_class='drive_test.parsers.nope.DoesNotExist',
            vendor='', file_extensions=['.xyz'], magic_bytes='',
            header_signature='', default_config={}, is_active=True,
        )
        self.drive_file.parser_profile = profile
        with self.assertRaises(ValueError) as ctx:
            _resolve_parser(self.drive_file)
        self.assertIn('unloadable', str(ctx.exception))

    def test_valid_profile_resolves(self):
        from drive_test.parsers.trp_parser import TrpDriveTestParser
        from drive_test.tasks import _resolve_parser

        if self.profile is None:
            self.skipTest('TRP ParserProfile not seeded')
        self.assertIsInstance(_resolve_parser(self.drive_file), TrpDriveTestParser)


class SessionAggregateTests(_PipelineFixture):
    """A session with no files must never report itself COMPLETED."""

    def test_session_with_no_files_is_not_completed(self):
        from drive_test.tasks import _update_session_aggregates

        empty = DriveTestSession.objects.create(
            operator=self.operator,
            test_date=timezone.now().date(),
            uploaded_by=self.user,
            status='PENDING',
        )
        _update_session_aggregates(empty)

        empty.refresh_from_db()
        self.assertNotEqual(
            empty.status, 'COMPLETED',
            'all() over an empty status set returned True',
        )
        self.assertEqual(empty.total_measurements, 0)


@override_settings(UMP_STORAGE_ROOT=tempfile.mkdtemp())
class IdempotencyTests(TestCase):
    """The same file content must not be ingested twice."""

    @classmethod
    def setUpTestData(cls):
        cls.operator = Operator.objects.create(code='orange', name='Orange', enabled=True)
        cls.user = get_user_model().objects.create_user(username='dedup', password='x')

    def test_restaging_identical_content_is_rejected(self):
        from drive_test.services.file_handler import sha256_of_file, stage_file

        with tempfile.NamedTemporaryFile(suffix='.trp', delete=False) as tmp:
            tmp.write(b'identical drive test payload')
            source = Path(tmp.name)

        try:
            stored, digest = stage_file(source, 'first.trp', 'orange')
            self.assertEqual(digest, sha256_of_file(source))

            session = DriveTestSession.objects.create(
                operator=self.operator,
                test_date=timezone.now().date(),
                uploaded_by=self.user,
                status='PROCESSING',
            )
            DriveTestFile.objects.create(
                session=session,
                original_filename='first.trp',
                file_path=str(stored),
                file_size=source.stat().st_size,
                sha256=digest,
            )

            with self.assertRaises(FileExistsError):
                stage_file(source, 'second.trp', 'orange')
        finally:
            source.unlink(missing_ok=True)
