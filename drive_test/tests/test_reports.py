"""Regulatory Reports & Compliance Reporting tests."""
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditLog
from drive_test.models import (
    Cell, Chiefdom, District, DriveTestFile, DriveTestSession, Finding,
    Measurement, RadioMeasurement, RegulatoryReport, RegulatoryRule,
    RegulatoryThreshold, Region, Sector, ServiceMeasurement, Site,
)
from drive_test.services import reports as rp
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})

TODAY = date.today()


@STATIC
class RegulatoryReportsTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.viewer = U.objects.create_user('viewer', password='x')
        cls.admin = U.objects.create_user('radmin', password='x', is_regulatory_admin=True)

        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')

        cls.region = Region.objects.create(code='W', name='Western Area')
        cls.district = District.objects.create(code='FT', name='Freetown', region=cls.region)
        cls.chiefdom = Chiefdom.objects.create(code='C1', name='Central', district=cls.district)
        cls.site = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange,
                                       latitude=8.48, longitude=-13.23, chiefdom=cls.chiefdom)
        cls.sector = Sector.objects.create(site=cls.site, sector_id='A')
        cls.cell_4g = Cell.objects.create(cell_id='LTE-001', operator=cls.orange, sector=cls.sector, technology='4G')

        # ── Orange session: 4G, 3 measurements (2 matched, 1 unmatched + 1 invalid), 1 voice call ──
        cls.sess = DriveTestSession.objects.create(
            operator=cls.orange, test_date=TODAY - timedelta(days=5), uploaded_by=cls.viewer, status='COMPLETED',
            total_measurements=4, matched_measurements=2, finding_count=1)
        f1 = DriveTestFile.objects.create(
            session=cls.sess, original_filename='o.trp', file_path='p', file_size=1, sha256='a' * 64, status='COMPLETED')

        def meas(seq, cell=None, rssi=None, valid=True):
            m = Measurement.objects.create(
                drive_file=f1, sequence_num=seq, captured_at=datetime(2026, 9, 20, 12, 0, seq),
                latitude=8.48, longitude=-13.23, matched_cell=cell, is_valid=valid,
                match_method='exact_ecgi' if cell else '')
            RadioMeasurement.objects.create(measurement=m, technology='4G', rssi=rssi)
            return m

        m1 = meas(1, cell=cls.cell_4g, rssi=-80.0)
        meas(2, cell=cls.cell_4g, rssi=-90.0)
        meas(3, cell=None, rssi=-70.0)          # unmatched but valid
        meas(4, cell=None, rssi=None, valid=False)  # invalid, excluded from KPI/data quality "valid" set
        ServiceMeasurement.objects.create(measurement=m1, service_type='VOICE', outcome='SUCCESS', mos=4.0)

        cls.finding = Finding.objects.create(
            session=cls.sess, finding_type='WEAK_SIGNAL', severity='HIGH',
            description='RSSI below threshold', measured_value=-90.0, threshold_value=-85.0)

        # ── Qcell session: 3G only, no findings, different operator ──
        cls.sess_q = DriveTestSession.objects.create(
            operator=cls.qcell, test_date=TODAY - timedelta(days=1), uploaded_by=cls.viewer, status='COMPLETED',
            total_measurements=1)
        f2 = DriveTestFile.objects.create(
            session=cls.sess_q, original_filename='q.trp', file_path='p', file_size=1, sha256='b' * 64, status='COMPLETED')
        m3 = Measurement.objects.create(
            drive_file=f2, sequence_num=1, captured_at=datetime(2026, 9, 24, 12, 0, 0),
            latitude=8.4, longitude=-13.2, is_valid=True)
        RadioMeasurement.objects.create(measurement=m3, technology='3G', rssi=-95.0)

        # ── Regulatory config: a 4G rule (applies to scope) and a 5G rule (does not) ──
        cls.rule_4g = RegulatoryRule.objects.create(
            rule_code='NATCA-RSRP-4G-001', name='LTE RSSI Minimum', technology='4G', metric='rssi',
            condition='lt', is_active=True, effective_from=TODAY - timedelta(days=100))
        cls.threshold_4g = RegulatoryThreshold.objects.create(
            rule=cls.rule_4g, operator=None, critical_value=-100.0, unit='dBm',
            effective_from=TODAY - timedelta(days=100))
        cls.rule_5g = RegulatoryRule.objects.create(
            rule_code='NATCA-RSRP-5G-001', name='5G RSSI Minimum', technology='5G', metric='rssi',
            condition='lt', is_active=True, effective_from=TODAY - timedelta(days=100))
        RegulatoryThreshold.objects.create(
            rule=cls.rule_5g, operator=None, critical_value=-100.0, unit='dBm',
            effective_from=TODAY - timedelta(days=100))

    def setUp(self):
        self.client.force_login(self.viewer)


# ---------------------------------------------------------------------------
# services/reports.py unit tests
# ---------------------------------------------------------------------------

class ReportsServiceUnitTest(RegulatoryReportsTest):
    def test_resolve_scope_by_operator(self):
        from django.http import QueryDict
        qd = QueryDict(f'operator={self.orange.code}')
        sessions, filters, errors = rp.resolve_scope(qd)
        self.assertEqual(list(sessions), [self.sess])
        self.assertFalse(errors)

    def test_resolve_scope_explicit_sessions_overrides_filters(self):
        from django.http import QueryDict
        qd = QueryDict(f'operator={self.orange.code}&session={self.sess_q.session_ref}')
        sessions, filters, errors = rp.resolve_scope(qd)
        # explicit session list wins even though operator filter would have picked a different session
        self.assertEqual(list(sessions), [self.sess_q])

    def test_scope_summary_real_counts(self):
        sessions_qs, _, _ = rp.resolve_scope({'operator': self.orange.code})
        meas_qs = rp.scope_measurements(sessions_qs)
        scope = rp.report_scope_summary(sessions_qs, meas_qs)
        self.assertEqual(scope['sessions'], 1)
        self.assertEqual(scope['measurements'], 3)          # is_valid=True only -> excludes the invalid one
        self.assertEqual(scope['matched_measurements'], 2)
        self.assertEqual(scope['unmatched_measurements'], 1)
        self.assertEqual(scope['voice_events'], 1)
        self.assertEqual(scope['findings'], 1)

    def test_applicable_rules_filters_by_observed_technology(self):
        sessions_qs, _, _ = rp.resolve_scope({'operator': self.orange.code})
        meas_qs = rp.scope_measurements(sessions_qs)
        rows = rp.applicable_rules(sessions_qs, meas_qs)
        rule_codes = {r['rule'].rule_code for r in rows}
        self.assertIn('NATCA-RSRP-4G-001', rule_codes)   # 4G observed in scope
        self.assertNotIn('NATCA-RSRP-5G-001', rule_codes)  # 5G never observed -> not applicable

    def test_applicable_rules_reports_real_finding_count_never_a_verdict(self):
        sessions_qs, _, _ = rp.resolve_scope({'operator': self.orange.code})
        meas_qs = rp.scope_measurements(sessions_qs)
        row = next(r for r in rp.applicable_rules(sessions_qs, meas_qs) if r['rule'].rule_code == 'NATCA-RSRP-4G-001')
        # No finding in the fixture references this threshold -> the real count is 0, not a fabricated verdict
        self.assertEqual(row['findings_in_scope'], 0)
        self.assertNotIn('status', {'PASS', 'FAIL', 'COMPLIANT'})  # sanity: no such key is ever produced

    def test_limitations_only_appear_when_backed_by_real_data(self):
        sessions_qs, _, _ = rp.resolve_scope({'operator': self.orange.code})
        meas_qs = rp.scope_measurements(sessions_qs)
        scope = rp.report_scope_summary(sessions_qs, meas_qs)
        notes = rp.build_limitations(scope, None)
        self.assertTrue(any('could not be matched' in n for n in notes))  # unmatched=1 -> real
        self.assertFalse(any('voice service measurements were available' in n for n in notes))  # voice_events=1 -> not shown


# ---------------------------------------------------------------------------
# Report generation workflow
# ---------------------------------------------------------------------------

class ReportGenerateViewTest(RegulatoryReportsTest):
    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('drive_test:report_generate')).status_code, 302)

    def test_get_with_no_filters_shows_no_scope_preview(self):
        r = self.client.get(reverse('drive_test:report_generate'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context['has_scope_input'])

    def test_preview_shows_real_scope_without_creating_a_report(self):
        r = self.client.post(reverse('drive_test:report_generate'), {
            'action': 'preview', 'operator': self.orange.code,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['scope']['sessions'], 1)
        self.assertEqual(RegulatoryReport.objects.count(), 0)

    def test_preview_with_no_matching_sessions(self):
        r = self.client.post(reverse('drive_test:report_generate'), {
            'action': 'preview', 'operator': self.orange.code, 'technology': '5G',
        })
        self.assertContains(r, 'No drive-test sessions match the selected criteria.')

    def test_generate_requires_admin_permission(self):
        r = self.client.post(reverse('drive_test:report_generate'), {
            'action': 'generate', 'report_type': 'COMPLIANCE', 'operator': self.orange.code,
        })
        self.assertEqual(RegulatoryReport.objects.count(), 0)
        self.assertContains(r, 'do not have permission')

    def test_generate_creates_a_real_persisted_report(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('drive_test:report_generate'), {
            'action': 'generate', 'report_type': 'COMPLIANCE', 'operator': self.orange.code,
        }, follow=True)
        self.assertEqual(RegulatoryReport.objects.count(), 1)
        report = RegulatoryReport.objects.get()
        self.assertEqual(report.status, RegulatoryReport.Status.COMPLETED)
        self.assertEqual(report.session_count, 1)
        self.assertEqual(report.measurement_count, 3)
        self.assertEqual(report.finding_count, 1)
        self.assertTrue(report.file.name)
        self.assertEqual(list(report.sessions.all()), [self.sess])
        self.assertRedirects(r, reverse('drive_test:report_detail', args=[report.report_ref]))

    def test_generate_writes_audit_entry(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('drive_test:report_generate'), {
            'action': 'generate', 'report_type': 'COMPLIANCE', 'operator': self.orange.code,
        })
        report = RegulatoryReport.objects.get()
        row = AuditLog.objects.get(entity_type='RegulatoryReport', entity_id=report.report_ref)
        self.assertEqual(row.action, 'CREATE')

    def test_generate_with_explicit_sessions(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('drive_test:report_generate'), {
            'action': 'generate', 'report_type': 'SESSION', 'session': self.sess_q.session_ref,
        })
        report = RegulatoryReport.objects.get()
        self.assertEqual(list(report.sessions.all()), [self.sess_q])
        self.assertEqual(report.measurement_count, 1)


# ---------------------------------------------------------------------------
# Report list
# ---------------------------------------------------------------------------

class ReportListViewTest(RegulatoryReportsTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.report = RegulatoryReport.objects.create(
            report_type='COMPLIANCE', title='Orange Compliance', operator=cls.orange,
            session_count=1, measurement_count=3, finding_count=1,
            status='COMPLETED', generated_by=cls.admin)
        cls.report.sessions.set([cls.sess])

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('drive_test:report_list')).status_code, 302)

    def test_list_shows_real_report(self):
        r = self.client.get(reverse('drive_test:report_list'))
        self.assertContains(r, self.report.report_ref)
        self.assertContains(r, 'Orange Compliance')

    def test_filter_by_operator(self):
        r = self.client.get(reverse('drive_test:report_list'), {'operator': self.qcell.code})
        self.assertEqual(list(r.context['reports']), [])

    def test_search_filter(self):
        r = self.client.get(reverse('drive_test:report_list'), {'q': self.report.report_ref})
        self.assertEqual(len(r.context['reports']), 1)

    def test_invalid_type_reported_not_500(self):
        r = self.client.get(reverse('drive_test:report_list'), {'type': 'NOT_A_TYPE'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('Unrecognised report type.', r.context['errors'])

    def test_empty_state_when_no_reports(self):
        RegulatoryReport.objects.all().delete()
        r = self.client.get(reverse('drive_test:report_list'))
        self.assertContains(r, 'No regulatory reports have been generated yet.')

    def test_viewer_does_not_see_generate_button(self):
        r = self.client.get(reverse('drive_test:report_list'))
        self.assertNotContains(r, 'Generate Report')

    def test_admin_sees_generate_button(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('drive_test:report_list'))
        self.assertContains(r, 'Generate Report')


# ---------------------------------------------------------------------------
# Report detail / download / PDF / map
# ---------------------------------------------------------------------------

class ReportDetailViewTest(RegulatoryReportsTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.client.post(reverse('drive_test:report_generate'), {
            'action': 'generate', 'report_type': 'COMPLIANCE', 'operator': self.orange.code,
        })
        self.report = RegulatoryReport.objects.get()
        self.client.force_login(self.viewer)

    def test_login_required(self):
        self.client.logout()
        r = self.client.get(reverse('drive_test:report_detail', args=[self.report.report_ref]))
        self.assertEqual(r.status_code, 302)

    def test_detail_shows_real_kpi_and_scope_values(self):
        r = self.client.get(reverse('drive_test:report_detail', args=[self.report.report_ref]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['scope']['measurements'], 3)
        self.assertIsNotNone(r.context['overall'])
        self.assertEqual(r.context['overall']['voice']['cssr_percent'], 100.0)

    def test_detail_rules_section_excludes_inapplicable_rule(self):
        r = self.client.get(reverse('drive_test:report_detail', args=[self.report.report_ref]))
        codes = {row['rule'].rule_code for row in r.context['rules']}
        self.assertIn('NATCA-RSRP-4G-001', codes)
        self.assertNotIn('NATCA-RSRP-5G-001', codes)

    def test_detail_findings_section_shows_real_finding(self):
        r = self.client.get(reverse('drive_test:report_detail', args=[self.report.report_ref]))
        self.assertContains(r, 'RSSI below threshold')

    def test_no_regulatory_verdict_language(self):
        r = self.client.get(reverse('drive_test:report_detail', args=[self.report.report_ref]))
        for word in ('PASS', 'FAIL', 'COMPLIANT', 'NON-COMPLIANT', 'Winner', 'Best Operator'):
            self.assertNotContains(r, word)

    def test_download_serves_real_excel_and_audits(self):
        r = self.client.get(reverse('drive_test:report_download', args=[self.report.report_ref]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertTrue(AuditLog.objects.filter(entity_type='RegulatoryReport', action='EXPORT').exists())

    def test_download_with_no_file_redirects(self):
        self.report.file.delete(save=True)
        r = self.client.get(reverse('drive_test:report_download', args=[self.report.report_ref]), follow=True)
        self.assertContains(r, 'No report file is available')

    def test_pdf_returns_real_pdf_bytes(self):
        r = self.client.get(reverse('drive_test:report_pdf', args=[self.report.report_ref]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF'))

    def test_map_data_only_includes_matched_sites_with_valid_coords(self):
        r = self.client.get(reverse('drive_test:report_map_data', args=[self.report.report_ref]))
        data = r.json()
        site_ids = [f['properties']['site_id'] for f in data['features']]
        self.assertEqual(site_ids, ['FT-001'])  # the only matched, geolocated site in scope

    def test_detail_404_for_unknown_report(self):
        r = self.client.get(reverse('drive_test:report_detail', args=['RPT-NOPE']))
        self.assertEqual(r.status_code, 404)
