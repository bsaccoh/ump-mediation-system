"""Phase 2 tests: parsing, detection, normalization, data quality, ingest.

Run: python manage.py test drive_test.tests.test_phase2 --settings=config.test_settings
"""
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from drive_test.models import Campaign, DriveTestFile, Project, Sample
from drive_test.models.enums import FileStatus
from drive_test.parsers import detect_format, detect_parser
from drive_test.parsers.csv_parser import CsvDriveTestParser
from drive_test.services.processing import process_file

User = get_user_model()
_MEDIA = tempfile.mkdtemp(prefix='dt-p2-media-')

_LTE_CSV = (
    "timestamp,latitude,longitude,technology,cellid,pci,earfcn,rsrp,rsrq,sinr,dl_mbps\n"
    "2026-09-01 10:00:00,8.4840,-13.2299,LTE,12345,101,1850,-85.2,-9.1,12.3,45.6\n"
    "2026-09-01 10:00:01,8.4841,-13.2300,LTE,12345,101,1850,-96.0,-12.0,4.0,10.1\n"
    "2026-09-01 10:00:02,8.4842,-13.2301,LTE,12346,102,1850,,-11.0,,\n"  # missing rsrp/sinr/dl
)


def _write(tmp, name, content, binary=False):
    p = Path(tmp) / name
    if binary:
        p.write_bytes(content)
    else:
        p.write_text(content)
    return str(p)


class ParserTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dt-parse-')

    def test_csv_detected_and_parsed(self):
        path = _write(self.tmp, 'lte.csv', _LTE_CSV)
        fmt, conf = detect_format(path)
        self.assertEqual(fmt, 'CSV')
        self.assertGreaterEqual(conf, 0.3)
        samples = list(CsvDriveTestParser().parse(path))
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0].technology, 'LTE')
        self.assertAlmostEqual(samples[0].rsrp, -85.2)
        self.assertAlmostEqual(samples[0].dl_throughput, 45600.0)  # 45.6 Mbps → kbps

    def test_missing_metric_is_none_not_zero(self):
        path = _write(self.tmp, 'lte.csv', _LTE_CSV)
        third = list(CsvDriveTestParser().parse(path))[2]
        self.assertIsNone(third.rsrp)
        self.assertIsNone(third.sinr)
        self.assertIsNone(third.dl_throughput)

    def test_technology_inferred_when_absent(self):
        csv = "time,lat,lon,ss_rsrp\n2026-09-01 10:00:00,8.48,-13.22,-90.0\n"
        path = _write(self.tmp, 'nr.csv', csv)
        s = list(CsvDriveTestParser().parse(path))[0]
        self.assertEqual(s.technology, 'NR')  # inferred from SS-RSRP

    def test_json_detected_and_parsed(self):
        doc = {'samples': [
            {'timestamp': '2026-09-01 10:00:00', 'lat': 8.48, 'lon': -13.22, 'rscp': -80, 'ecno': -6},
        ]}
        path = _write(self.tmp, 'umts.json', json.dumps(doc))
        fmt, _ = detect_format(path)
        self.assertEqual(fmt, 'JSON')
        s = list(detect_parser(path).parse(path))
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0].technology, 'UMTS')

    def test_unknown_format_returns_none(self):
        path = _write(self.tmp, 'note.txt', 'just some prose with no columns at all\n')
        self.assertIsNone(detect_parser(path))
        self.assertEqual(detect_format(path)[0], 'UNKNOWN')


@override_settings(MEDIA_ROOT=_MEDIA)
class IngestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('op', password='x', is_operator=True)
        self.project = Project.objects.create(name='P', created_by=self.user)
        self.campaign = Campaign.objects.create(project=self.project, name='C')
        self.tmp = tempfile.mkdtemp(prefix='dt-ingest-')

    def _make_file(self, content=_LTE_CSV, name='lte.csv'):
        path = _write(self.tmp, name, content)
        return DriveTestFile.objects.create(
            campaign=self.campaign, original_name=name, stored_path=path,
            size_bytes=len(content), sha256='a' * 64, status=FileStatus.READY,
        )

    def test_process_creates_samples_and_completes(self):
        dtf = self._make_file()
        result = process_file(dtf.pk)
        dtf.refresh_from_db()
        self.assertEqual(dtf.status, FileStatus.COMPLETED)
        self.assertEqual(dtf.sample_count, 3)
        self.assertEqual(Sample.objects.filter(drive_file=dtf).count(), 3)
        self.assertEqual(result['samples'], 3)
        self.assertEqual(dtf.detected_technology, 'LTE')

    def test_missing_metric_persisted_as_null(self):
        dtf = self._make_file()
        process_file(dtf.pk)
        third = Sample.objects.filter(drive_file=dtf).order_by('timestamp')[2]
        self.assertIsNone(third.rsrp)   # NULL, never 0
        self.assertIsNone(third.sinr)

    def test_quality_score_computed(self):
        dtf = self._make_file()
        process_file(dtf.pk)
        dtf.refresh_from_db()
        self.assertIsNotNone(dtf.quality_score)
        self.assertEqual(dtf.quality_report['total_samples'], 3)
        self.assertEqual(dtf.quality_report['gps_coverage_pct'], 100.0)

    def test_reprocess_is_idempotent(self):
        dtf = self._make_file()
        process_file(dtf.pk)
        process_file(dtf.pk)  # again
        self.assertEqual(Sample.objects.filter(drive_file=dtf).count(), 3)

    def test_operator_resolved_from_plmn(self):
        from reference.models import Operator
        Operator.objects.create(code='orange', name='Orange', home_plmn='61901',
                                home_mcc='619', home_mnc='01')
        csv = ("time,lat,lon,mcc,mnc,rsrp\n"
               "2026-09-01 10:00:00,8.48,-13.22,619,01,-85\n")
        dtf = self._make_file(content=csv, name='op.csv')
        process_file(dtf.pk)
        s = Sample.objects.get(drive_file=dtf)
        self.assertIsNotNone(s.operator)
        self.assertEqual(s.operator.code, 'orange')

    def test_empty_file_fails_gracefully(self):
        dtf = self._make_file(content="timestamp,latitude,longitude,rsrp\n", name='empty.csv')
        process_file(dtf.pk)
        dtf.refresh_from_db()
        self.assertEqual(dtf.status, FileStatus.FAILED)
        self.assertIsNone(dtf.quality_score)  # empty → None, not 0
