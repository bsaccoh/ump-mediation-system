"""
Parser detection registry.

Resolution picks the highest sniff() confidence rather than the first extension
match. That matters because .csv, .txt and .log collide across formats in this
domain: first-match-wins decides on whichever ParserProfile happened to be
created first, which silently mis-parses files the moment a second format is
added. These tests pin the ordering so adding a format cannot quietly steal
another's files.

Also covers the capability declarations, which let the workspace distinguish
"this drive recorded none" from "this format cannot carry it".
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from drive_test.models import DriveTestFile, DriveTestSession, ParserProfile
from drive_test.parsers.base import DriveTestParser, ParserCapabilities
from drive_test.parsers.csv_parser import CsvDriveTestParser
from drive_test.parsers.trp_parser import TrpDriveTestParser
from drive_test.services.file_handler import (
    capabilities_for, detect_parser, load_parser_class, score_parsers,
)
from reference.models import Operator

_CSV_HEADER = 'timestamp,latitude,longitude,rsrp,sinr,technology\n'
_CSV_ROW = '2026-01-18T12:00:00Z,8.484,-13.23,-95.5,12.0,4G\n'


def _write(suffix: str, data: bytes) -> Path:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        return Path(tmp.name)


def _write_trp(members: dict[str, bytes] | None = None) -> Path:
    path = _write('.trp', b'')
    with zipfile.ZipFile(path, 'w') as zf:
        for name, body in (members or {
            'wptrack.xml': b'<track/>',
            'data.cdf': b'\x00',
            'content.xml': b'<content/>',
            'services.xml': b'<services/>',
        }).items():
            zf.writestr(name, body)
    return path


class SniffTests(TestCase):
    """Each parser scores itself honestly."""

    def tearDown(self):
        for path in getattr(self, '_paths', []):
            path.unlink(missing_ok=True)

    def _track(self, path):
        self._paths = getattr(self, '_paths', [])
        self._paths.append(path)
        return path

    def test_trp_scores_high_on_a_real_archive(self):
        path = self._track(_write_trp())
        self.assertGreaterEqual(TrpDriveTestParser.sniff(path), 0.9)

    def test_trp_rejects_a_zip_that_is_not_a_trp(self):
        """A .docx and an .apk are also ZIPs. Extension plus PK is not enough."""
        path = self._track(_write_trp({'word/document.xml': b'<w/>'}))
        self.assertEqual(TrpDriveTestParser.sniff(path), 0.0)

    def test_trp_rejects_a_non_zip_with_the_right_extension(self):
        path = self._track(_write('.trp', b'this is not a zip archive'))
        self.assertEqual(TrpDriveTestParser.sniff(path), 0.0)

    def test_trp_rejects_the_wrong_extension(self):
        path = self._track(_write('.csv', b'PK\x03\x04'))
        self.assertEqual(TrpDriveTestParser.sniff(path), 0.0)

    def test_csv_scores_on_recognised_headers(self):
        path = self._track(_write('.csv', (_CSV_HEADER + _CSV_ROW).encode()))
        self.assertGreater(CsvDriveTestParser.sniff(path), 0.3)

    def test_csv_rejects_an_unrecognisable_header(self):
        """A delimited file is not automatically a drive test."""
        path = self._track(_write('.csv', b'foo,bar,baz\n1,2,3\n'))
        self.assertEqual(CsvDriveTestParser.sniff(path), 0.0)

    def test_csv_handles_semicolon_and_tab_delimiters(self):
        for delimiter in (';', '\t'):
            header = _CSV_HEADER.strip().replace(',', delimiter) + '\n'
            path = self._track(_write('.csv', header.encode()))
            self.assertGreater(
                CsvDriveTestParser.sniff(path), 0.3,
                f'delimiter {delimiter!r} was not recognised',
            )

    def test_csv_rejects_an_empty_file(self):
        path = self._track(_write('.csv', b''))
        self.assertEqual(CsvDriveTestParser.sniff(path), 0.0)

    def test_a_structural_match_outranks_an_extension_match(self):
        """The property the registry depends on: content beats extension."""
        trp = self._track(_write_trp())
        csv_path = self._track(_write('.csv', (_CSV_HEADER + _CSV_ROW).encode()))
        self.assertGreater(
            TrpDriveTestParser.sniff(trp), CsvDriveTestParser.sniff(csv_path),
            'a format confirming its own structure must outrank a header guess',
        )

    def test_sniff_never_raises_on_a_missing_file(self):
        missing = Path(tempfile.gettempdir()) / 'definitely-not-here.trp'
        self.assertEqual(TrpDriveTestParser.sniff(missing), 0.0)
        self.assertEqual(CsvDriveTestParser.sniff(missing), 0.0)


class DetectionTests(TestCase):
    """Resolution against the seeded ParserProfile rows."""

    def setUp(self):
        self._paths: list[Path] = []

    def tearDown(self):
        for path in self._paths:
            path.unlink(missing_ok=True)

    def _track(self, path):
        self._paths.append(path)
        return path

    def test_trp_resolves_to_the_trp_profile(self):
        profile = detect_parser(self._track(_write_trp()))
        self.assertIsNotNone(profile)
        self.assertIn('TRP', profile.name.upper())

    def test_csv_resolves_to_the_csv_profile(self):
        path = self._track(_write('.csv', (_CSV_HEADER + _CSV_ROW).encode()))
        profile = detect_parser(path)
        self.assertIsNotNone(profile)
        self.assertIn('CSV', profile.name.upper())

    def test_unknown_format_resolves_to_nothing(self):
        """Must return None, never fall back to a guess."""
        path = self._track(_write('.bin', b'\x00\x01\x02\x03binary payload'))
        self.assertIsNone(detect_parser(path))

    def test_unrecognised_csv_resolves_to_nothing(self):
        path = self._track(_write('.csv', b'alpha,beta\n1,2\n'))
        self.assertIsNone(detect_parser(path))

    def test_scoring_is_ordered_best_first(self):
        scored = score_parsers(self._track(_write_trp()))
        self.assertTrue(scored)
        self.assertEqual(scored, sorted(scored, key=lambda p: p[0], reverse=True))

    def test_a_broken_profile_does_not_break_detection(self):
        """One unloadable profile must not stop the others resolving."""
        ParserProfile.objects.create(
            name='Broken', parser_class='drive_test.parsers.nope.Missing',
            vendor='', file_extensions=['.trp'], magic_bytes='',
            header_signature='', default_config={}, is_active=True,
        )
        profile = detect_parser(self._track(_write_trp()))
        self.assertIsNotNone(profile)
        self.assertIn('TRP', profile.name.upper())

    def test_inactive_profiles_are_ignored(self):
        ParserProfile.objects.filter(
            parser_class__endswith='TrpDriveTestParser'
        ).update(is_active=False)
        self.assertIsNone(detect_parser(self._track(_write_trp())))

    def test_load_parser_class_returns_none_for_a_bad_path(self):
        profile = ParserProfile.objects.create(
            name='Bad path', parser_class='nope.NotReal', vendor='',
            file_extensions=['.x'], magic_bytes='', header_signature='',
            default_config={}, is_active=True,
        )
        self.assertIsNone(load_parser_class(profile))


class CapabilityTests(TestCase):
    """Capabilities describe the FORMAT, not one file."""

    def test_trp_declares_what_it_extracts(self):
        caps = TrpDriveTestParser.capabilities
        self.assertIn('rssi', caps.metrics)
        self.assertIn('rxqual', caps.metrics)
        # The TRP export carries no neighbour list, and the parser produces
        # neither carriers nor beams. Claiming otherwise would make the UI
        # report a format limitation as a quiet drive.
        self.assertFalse(caps.neighbours)
        self.assertFalse(caps.carriers)
        self.assertFalse(caps.beams)
        self.assertTrue(caps.voice_quality)

    def test_trp_does_not_claim_lte_or_nr_metrics(self):
        caps = TrpDriveTestParser.capabilities
        for metric in ('rsrp', 'rsrq', 'sinr', 'ss_rsrp', 'dl_kbps'):
            self.assertFalse(caps.supports(metric), f'{metric} should be unsupported')

    def test_csv_makes_no_metric_claim(self):
        """A generic importer maps whatever columns exist, so it claims nothing."""
        caps = CsvDriveTestParser.capabilities
        self.assertEqual(caps.metrics, frozenset())
        # No claim must never mark anything unsupported.
        for metric in ('rsrp', 'ss_rsrp', 'rxqual', 'anything_at_all'):
            self.assertTrue(caps.supports(metric))

    def test_base_parser_makes_no_claim(self):
        self.assertTrue(DriveTestParser.capabilities.supports('rsrp'))

    def test_capabilities_for_resolves_from_a_profile(self):
        profile = ParserProfile.objects.filter(
            parser_class__endswith='TrpDriveTestParser'
        ).first()
        caps = capabilities_for(profile)
        self.assertIsInstance(caps, ParserCapabilities)
        self.assertIn('rssi', caps.metrics)


class UnsupportedMetricReportingTests(TestCase):
    """The payload must separate a quiet drive from a format limitation."""

    @classmethod
    def setUpTestData(cls):
        cls.operator = Operator.objects.create(code='orange', name='Orange', enabled=True)
        cls.user = get_user_model().objects.create_user(username='caps', password='pw')
        cls.trp_profile = ParserProfile.objects.filter(
            parser_class__endswith='TrpDriveTestParser'
        ).first()

    def _session(self, profile):
        session = DriveTestSession.objects.create(
            operator=self.operator, test_date=timezone.now().date(),
            uploaded_by=self.user, status='COMPLETED',
        )
        DriveTestFile.objects.create(
            session=session, original_filename='x.trp', file_path='/x',
            file_size=1, sha256=f'{session.pk:064d}', parser_profile=profile,
        )
        return session

    def test_format_limits_are_reported_separately(self):
        from drive_test.services.timeseries import session_timeseries

        meta = session_timeseries(self._session(self.trp_profile))['meta']

        # TRP cannot carry LTE or NR metrics, so their absence is a property of
        # the format rather than of the drive.
        self.assertIn('rsrp', meta['unsupported_metrics'])
        self.assertIn('ss_rsrp', meta['unsupported_metrics'])
        # Unsupported is always a subset of absent — never a separate universe.
        for metric in meta['unsupported_metrics']:
            self.assertIn(metric, meta['absent_metrics'])

    def test_metrics_the_format_supports_are_not_marked_unsupported(self):
        from drive_test.services.timeseries import session_timeseries

        meta = session_timeseries(self._session(self.trp_profile))['meta']
        self.assertNotIn('rssi', meta['unsupported_metrics'])
        self.assertNotIn('rxqual', meta['unsupported_metrics'])

    def test_a_session_with_no_profile_claims_nothing(self):
        from drive_test.services.timeseries import session_timeseries

        meta = session_timeseries(self._session(None))['meta']
        self.assertEqual(meta['unsupported_metrics'], [])
