"""
Drive Test pipeline smoke tests.

Tests run entirely in-memory / SQLite — no Celery, no real files.
"""
import io
import tempfile
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class CsvParserTest(TestCase):
    """Parse a small in-memory CSV and verify ParsedMeasurement fields."""

    CSV_CONTENT = """\
timestamp,latitude,longitude,technology,mcc,mnc,tac,eci,pci,earfcn,rsrp,rsrq,sinr,dl_throughput_kbps
2024-01-15 08:00:00,8.4897,-13.2344,4G,619,02,1234,5678901,123,1300,-85.5,-10.2,12.3,15000
2024-01-15 08:00:05,8.4900,-13.2340,4G,619,02,1234,5678902,124,1300,-110.0,-15.0,3.0,500
2024-01-15 08:00:10,,,-,-,-,,,,,,,
"""

    def test_parse_valid_rows(self):
        from drive_test.parsers.csv_parser import CsvDriveTestParser

        parser = CsvDriveTestParser()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(self.CSV_CONTENT)
            tmp = Path(f.name)

        try:
            rows = list(parser.parse(tmp))
        finally:
            tmp.unlink(missing_ok=True)

        self.assertEqual(len(rows), 3)

        # Row 1: all fields present
        r1 = rows[0]
        self.assertAlmostEqual(r1.latitude, 8.4897)
        self.assertAlmostEqual(r1.longitude, -13.2344)
        self.assertEqual(r1.technology, '4G')
        self.assertEqual(r1.obs_mcc, '619')
        self.assertEqual(r1.obs_eci, 5678901)
        self.assertEqual(r1.obs_pci, 123)
        self.assertAlmostEqual(r1.rsrp, -85.5)
        self.assertTrue(r1.is_valid)

        # Row 2: weak signal
        r2 = rows[1]
        self.assertAlmostEqual(r2.rsrp, -110.0)
        self.assertTrue(r2.is_valid)

        # Row 3: missing GPS
        r3 = rows[2]
        self.assertFalse(r3.is_valid)
        self.assertIn('NO_GPS', r3.quality_flags)

    def test_unknown_columns_go_to_raw_data(self):
        from drive_test.parsers.csv_parser import CsvDriveTestParser

        csv = "timestamp,latitude,longitude,custom_vendor_field\n2024-01-15 08:00:00,8.49,-13.23,ABC\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(csv)
            tmp = Path(f.name)

        try:
            rows = list(CsvDriveTestParser().parse(tmp))
        finally:
            tmp.unlink(missing_ok=True)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].raw_data.get('custom_vendor_field'), 'ABC')


class CellMatcherTest(TestCase):
    """CellReferenceMatcher — exact CGI match strategy."""

    def setUp(self):
        from reference.models import Operator
        from drive_test.models import Region, Site, Sector, Cell

        self.operator = Operator.objects.create(
            code='testop', name='Test Operator', home_mcc='619', home_mnc='99'
        )
        region = Region.objects.create(code='WR', name='Western Region')
        site = Site.objects.create(
            site_id='TEST001', operator=self.operator, name='Test Site',
            latitude=8.49, longitude=-13.23,
        )
        sector = Sector.objects.create(site=site, sector_id='S0')
        self.cell = Cell.objects.create(
            cell_id='C001', operator=self.operator, sector=sector,
            technology='4G', mcc='619', mnc='99', tac=1234, eci=5678901,
            pci=123, earfcn=1300,
            latitude=8.49, longitude=-13.23,
        )

    def test_exact_ecgi_match(self):
        from drive_test.models import DriveTestSession, DriveTestFile, Measurement
        from drive_test.services.cell_matcher import CellReferenceMatcher

        session = DriveTestSession.objects.create(
            operator=self.operator,
            test_date=date(2024, 1, 15),
            uploaded_by=User.objects.create_user('tester', password='x'),
        )
        drive_file = DriveTestFile.objects.create(
            session=session,
            original_filename='test.csv',
            file_path='/tmp/test.csv',
            file_size=100,
            sha256='a' * 64,
        )
        from django.utils import timezone
        m = Measurement.objects.create(
            drive_file=drive_file,
            sequence_num=1,
            captured_at=timezone.now(),
            latitude=8.49,
            longitude=-13.23,
            obs_mcc='619',
            obs_mnc='99',
            obs_eci=5678901,
        )

        matcher = CellReferenceMatcher(operator_id=self.operator.id)
        count = matcher.match_bulk(Measurement.objects.filter(pk=m.pk))

        m.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(m.matched_cell, self.cell)
        self.assertEqual(m.match_method, 'exact_ecgi')
        self.assertAlmostEqual(m.match_confidence, 1.0)


class DataQualityAssessorTest(TestCase):
    """QualityAssessor computes scores correctly."""

    def test_perfect_quality(self):
        from reference.models import Operator
        from drive_test.models import Region, Site, Sector, Cell, DriveTestSession, DriveTestFile, Measurement
        from drive_test.services.analysis import QualityAssessor
        from django.utils import timezone

        op = Operator.objects.create(code='qop', name='Q Op', home_mcc='619', home_mnc='01')
        session = DriveTestSession.objects.create(
            operator=op, test_date=date(2024, 1, 1),
            uploaded_by=User.objects.create_user('q_user', password='x'),
        )
        drive_file = DriveTestFile.objects.create(
            session=session, original_filename='q.csv',
            file_path='/tmp/q.csv', file_size=10, sha256='b' * 64,
        )
        site = Site.objects.create(site_id='Q01', operator=op, name='Q', latitude=8.0, longitude=-13.0)
        sector = Sector.objects.create(site=site, sector_id='S0')
        cell = Cell.objects.create(
            cell_id='QC1', operator=op, sector=sector, technology='4G',
            latitude=8.0, longitude=-13.0,
        )

        for i in range(5):
            Measurement.objects.create(
                drive_file=drive_file, sequence_num=i + 1,
                captured_at=timezone.now(),
                latitude=8.0 + i * 0.001,
                longitude=-13.0 + i * 0.001,
                matched_cell=cell,
                obs_mcc='619', obs_mnc='01',
            )

        result = QualityAssessor().assess(drive_file)
        self.assertEqual(result.total_records, 5)
        self.assertEqual(result.matched_cells, 5)
        self.assertAlmostEqual(result.match_rate_pct, 100.0)
        self.assertGreater(result.overall_score, 50.0)
