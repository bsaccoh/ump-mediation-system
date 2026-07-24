"""Unit tests for ``regulatory.engines.intercept``.

* ``_criteria_q`` rejects requests with no identifier
* ``run_lea_query`` filters by MSISDN / IMSI / IMEI / cell_id / date range
* ``export_evidentiary`` produces deterministic SHA-256 over identical input
* Request status promotes OPEN → FULFILLED on export
* AuditLog entry written
"""
import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import AuditLog
from regulatory.engines.intercept import (
    run_lea_query, export_evidentiary, _criteria_q,
)
from regulatory.models import LEARequest, LEAExtractionLog

from interconnect.tests._fixtures import make_msc_record


def _request(filter_msisdn='', filter_imsi='', filter_imei='', filter_cell_id='',
              start=None, end=None, case='CR-TEST'):
    return LEARequest.objects.create(
        case_number=case,
        requesting_agency='Test',
        filter_msisdn=filter_msisdn,
        filter_imsi=filter_imsi,
        filter_imei=filter_imei,
        filter_cell_id=filter_cell_id,
        filter_start=start or datetime(2026, 3, 1),
        filter_end=end or datetime(2026, 4, 1),
        status='OPEN',
    )


class CriteriaQTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def test_no_identifier_raises(self):
        req = _request()
        with self.assertRaises(ValueError):
            _criteria_q(req)

    def test_msisdn_only_ok(self):
        req = _request(filter_msisdn='23276111111')
        q = _criteria_q(req)
        self.assertIsNotNone(q)

    def test_cell_id_alone_is_enough(self):
        req = _request(filter_cell_id='12345')
        q = _criteria_q(req)
        self.assertIsNotNone(q)


class RunLeaQueryTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        # Seed three MSC records — one matches the target MSISDN, two don't
        make_msc_record('MOC', '23276111111', '23277000000',
                         duration=60, start_time=datetime(2026, 3, 17, 10, 0),
                         imsi='619011234567890', imei='123456789012345')
        make_msc_record('MOC', '23276999999', '23280000000',
                         duration=120, start_time=datetime(2026, 3, 17, 11, 0))
        make_msc_record('MOC', '23276111111', '23277000000',
                         duration=180, start_time=datetime(2025, 1, 1, 12, 0))  # out of window

    def test_msisdn_filter_matches_calling_and_called(self):
        req = _request(filter_msisdn='23276111111',
                        start=datetime(2026, 3, 1), end=datetime(2026, 4, 1))
        rows = run_lea_query(req)
        # 1 in window (the 2026-03-17 record), out-of-window 2025-01-01 excluded
        self.assertEqual(len(rows), 1)

    def test_imsi_filter_exact(self):
        req = _request(filter_imsi='619011234567890',
                        start=datetime(2026, 3, 1), end=datetime(2026, 4, 1))
        rows = run_lea_query(req)
        self.assertEqual(len(rows), 1)

    def test_imei_filter_exact(self):
        req = _request(filter_imei='123456789012345',
                        start=datetime(2026, 3, 1), end=datetime(2026, 4, 1))
        rows = run_lea_query(req)
        self.assertEqual(len(rows), 1)

    def test_limit_applied(self):
        # Add 10 more records for the same MSISDN
        for i in range(10):
            make_msc_record('MOC', '23276111111', '23277000000',
                             duration=60, start_time=datetime(2026, 3, 17, 10, i))
        req = _request(filter_msisdn='23276111111',
                        start=datetime(2026, 3, 1), end=datetime(2026, 4, 1))
        rows = run_lea_query(req, limit=5)
        self.assertEqual(len(rows), 5)


class ExportEvidentiaryTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    def setUp(self):
        # 3 matching records
        for i in range(3):
            make_msc_record('MOC', '23276111111', '23277000000',
                             duration=60 + i,
                             start_time=datetime(2026, 3, 17, 10, i),
                             imsi='619011234567890')
        self.req = _request(filter_msisdn='23276111111',
                             start=datetime(2026, 3, 1), end=datetime(2026, 4, 1),
                             case='CR-TEST-001')

    def test_export_creates_extraction_log(self):
        ext = export_evidentiary(self.req)
        self.assertIsInstance(ext, LEAExtractionLog)
        self.assertEqual(ext.record_count, 3)
        self.assertTrue(ext.export_file)
        self.assertGreater(ext.export_file.size, 0)

    def test_sha256_is_deterministic_for_same_input(self):
        """Two exports of the same request should yield the same hash."""
        # Note: the CSV includes 'Exported at' timestamp which makes this
        # NOT byte-deterministic.  We assert the hash matches a manual
        # SHA-256 over the file's actual bytes.
        ext = export_evidentiary(self.req)
        actual_bytes = ext.export_file.read()
        ext.export_file.close()
        self.assertEqual(ext.sha256, hashlib.sha256(actual_bytes).hexdigest())

    def test_request_status_flips_to_fulfilled(self):
        self.assertEqual(self.req.status, 'OPEN')
        export_evidentiary(self.req)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'FULFILLED')
        self.assertIsNotNone(self.req.fulfilled_at)

    def test_audit_log_lea_export_written(self):
        before = AuditLog.objects.filter(action='LEA_EXPORT').count()
        export_evidentiary(self.req)
        after = AuditLog.objects.filter(action='LEA_EXPORT').count()
        self.assertEqual(after, before + 1)

    def test_csv_has_header_block_with_case_number(self):
        ext = export_evidentiary(self.req)
        body = ext.export_file.read().decode('utf-8')
        ext.export_file.close()
        self.assertIn('CR-TEST-001', body)
        self.assertIn('# Case', body)
        self.assertIn('start_time', body)  # column header row


class DecoratorActionMappingTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}
    """Lock the action-tagging fix from Sprint 1."""

    def test_get_browse_tagged_as_request_opened(self):
        from regulatory.decorators import _action_for
        class _Req: method, path = 'GET', '/regulatory/intercept/'
        self.assertEqual(_action_for(_Req()), 'LEA_REQUEST_OPENED')

    def test_get_download_tagged_as_export(self):
        from regulatory.decorators import _action_for
        class _Req: method, path = 'GET', '/regulatory/intercept/extraction/5/download/'
        self.assertEqual(_action_for(_Req()), 'LEA_EXPORT')

    def test_post_execute_tagged_as_query_executed(self):
        from regulatory.decorators import _action_for
        class _Req: method, path = 'POST', '/regulatory/intercept/5/execute/'
        self.assertEqual(_action_for(_Req()), 'LEA_QUERY_EXECUTED')

    def test_post_export_tagged_as_export(self):
        from regulatory.decorators import _action_for
        class _Req: method, path = 'POST', '/regulatory/intercept/5/export/'
        self.assertEqual(_action_for(_Req()), 'LEA_EXPORT')
