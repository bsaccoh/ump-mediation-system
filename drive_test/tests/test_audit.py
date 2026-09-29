"""Audit Trail workspace tests."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditLog
from drive_test.models import (
    Cell, CellHistory, DriveTestFile, DriveTestSession, Finding, FrequencyBand,
    RegulatoryRule, RegulatoryThreshold, Sector, Site,
)
from drive_test.services import audit as al
from drive_test import tasks as dt_tasks
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})

TODAY = date.today()


# ---------------------------------------------------------------------------
# Unit tests for services/audit.py itself
# ---------------------------------------------------------------------------

class AuditServiceUnitTest(TestCase):
    def test_log_writes_a_real_auditlog_row(self):
        al.log(action='CREATE', entity_type='RegulatoryRule', entity_id='7',
               description='Rule created: TEST-1', fields={'name': 'Test'})
        row = AuditLog.objects.get(entity_type='RegulatoryRule', entity_id='7')
        self.assertEqual(row.action, 'CREATE')
        self.assertEqual(row.extra_data['fields']['name'], 'Test')

    def test_log_masks_sensitive_keys(self):
        al.log(action='UPDATE', entity_type='Site', entity_id='1', description='x',
               changes={'password': {'from': 'a', 'to': 'b'}})
        row = AuditLog.objects.get(entity_type='Site', entity_id='1')
        self.assertEqual(row.extra_data['changes']['password'], '\u2022' * 8)

    def test_log_never_raises_on_bad_input(self):
        # A non-serialisable object anywhere in extra must not crash the caller.
        class Weird:
            pass
        try:
            al.log(action='CREATE', entity_type='Site', entity_id='2', description='x', weird=Weird())
        except Exception as exc:  # pragma: no cover - the point of the test is that this doesn't happen
            self.fail(f'log() raised: {exc}')

    def test_changes_dict_only_includes_real_differences(self):
        before = {'a': 1, 'b': 2, 'c': 3}
        after = {'a': 1, 'b': 20, 'c': 3}
        self.assertEqual(al.changes_dict(before, after), {'b': {'from': 2, 'to': 20}})

    def test_changes_dict_serialises_dates(self):
        before = {'effective_to': None}
        after = {'effective_to': TODAY}
        out = al.changes_dict(before, after)
        self.assertEqual(out['effective_to']['to'], TODAY.isoformat())

    def test_sanitize_error_strips_paths(self):
        msg = al.sanitize_error(r'FileNotFoundError: not found: C:\Users\bob\file.trp')
        self.assertNotIn('C:\\Users', msg)
        self.assertIn('FileNotFoundError', msg)

    def test_has_any_entries_false_when_empty(self):
        self.assertFalse(al.has_any_entries())
        al.log(action='CREATE', entity_type='Site', entity_id='9', description='x')
        self.assertTrue(al.has_any_entries())


# ---------------------------------------------------------------------------
# Instrumentation: real views write real audit rows
# ---------------------------------------------------------------------------

@STATIC
class AuditInstrumentationTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user('radmin', password='x', is_regulatory_admin=True)
        cls.op = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.rule = RegulatoryRule.objects.create(
            rule_code='NATCA-RSRP-001', name='RSRP Rule', metric='rsrp', condition='lt',
            is_active=True, effective_from=TODAY - timedelta(days=10))
        cls.site = Site.objects.create(site_id='S1', name='Site One', operator=cls.op)
        cls.band = FrequencyBand.objects.create(band_number='3', technology='4G', frequency_mhz=1800)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_rule_create_is_audited(self):
        self.client.post(reverse('drive_test:rule_create'), {
            'rule_code': 'NEW-1', 'name': 'New Rule', 'metric': 'rsrp', 'condition': 'lt',
            'is_active': 'on', 'effective_from': TODAY.isoformat(),
        })
        row = AuditLog.objects.get(entity_type='RegulatoryRule', entity_id=str(RegulatoryRule.objects.get(rule_code='NEW-1').pk))
        self.assertEqual(row.action, 'CREATE')
        self.assertIn('NEW-1', row.description)

    def test_rule_update_is_audited_with_changes(self):
        self.client.post(reverse('drive_test:rule_edit', args=[self.rule.pk]), {
            'rule_code': self.rule.rule_code, 'name': 'RSRP Rule (revised)', 'metric': 'rsrp',
            'condition': 'lt', 'is_active': 'on', 'effective_from': self.rule.effective_from.isoformat(),
        })
        row = AuditLog.objects.get(entity_type='RegulatoryRule', entity_id=str(self.rule.pk), action='UPDATE')
        self.assertIn('name', row.extra_data['changes'])
        self.assertEqual(row.extra_data['changes']['name']['to'], 'RSRP Rule (revised)')

    def test_rule_toggle_is_audited(self):
        self.client.post(reverse('drive_test:rule_toggle_active', args=[self.rule.pk]))
        row = AuditLog.objects.get(entity_type='RegulatoryRule', entity_id=str(self.rule.pk))
        self.assertFalse(row.extra_data['changes']['is_active']['to'])

    def test_threshold_create_and_expire_are_audited(self):
        self.client.post(reverse('drive_test:threshold_create'), {
            'rule': self.rule.pk, 'operator': '', 'critical_value': '-100', 'unit': 'dBm',
            'effective_from': TODAY.isoformat(),
        })
        threshold = RegulatoryThreshold.objects.get(rule=self.rule)
        create_row = AuditLog.objects.get(entity_type='RegulatoryThreshold', action='CREATE')
        self.assertIn('NATCA-RSRP-001', create_row.description)

        self.client.post(reverse('drive_test:threshold_expire', args=[threshold.pk]))
        expire_row = AuditLog.objects.get(entity_type='RegulatoryThreshold', action='UPDATE')
        self.assertEqual(expire_row.extra_data['changes']['effective_to']['to'], TODAY.isoformat())

    def test_site_create_and_update_are_audited(self):
        self.client.post(reverse('drive_test:site_create'), {
            'site_id': 'S-2', 'name': 'Site Two', 'operator': self.op.pk, 'site_type': 'macro',
            'latitude': '8.5', 'longitude': '-13.2', 'is_active': 'on',
        })
        site = Site.objects.get(site_id='S-2')
        self.assertTrue(AuditLog.objects.filter(entity_type='Site', entity_id=str(site.pk), action='CREATE').exists())

        self.client.post(reverse('drive_test:site_edit', args=[site.pk]), {
            'site_id': 'S-2', 'name': 'Site Two Renamed', 'operator': self.op.pk, 'site_type': 'macro',
            'latitude': '8.5', 'longitude': '-13.2', 'is_active': 'on',
        })
        row = AuditLog.objects.get(entity_type='Site', entity_id=str(site.pk), action='UPDATE')
        self.assertEqual(row.extra_data['changes']['name']['to'], 'Site Two Renamed')

    def test_sector_create_is_audited(self):
        self.client.post(reverse('drive_test:sector_create'), {
            'operator': self.op.pk, 'site_code': 'S1', 'sector_id': 'A', 'azimuth_deg': '120', 'is_active': 'on',
        })
        sector = Sector.objects.get(sector_id='A')
        self.assertTrue(AuditLog.objects.filter(entity_type='Sector', entity_id=str(sector.pk)).exists())

    def test_cell_create_writes_cell_history_not_a_duplicate_auditlog_row(self):
        """Cell changes already have their own real audit trail (CellHistory) — this
        page must read that, not write a second, duplicate AuditLog row for cells."""
        self.client.post(reverse('drive_test:cell_create'), {
            'cell_id': 'C-1', 'operator': self.op.pk, 'technology': '4G', 'site_code': 'S1',
            'sector_code': 'A', 'mcc': '619', 'mnc': '01', 'tac': '100', 'eci': '555',
            'pci': '10', 'earfcn': '1650', 'band': self.band.pk, 'is_active': 'on',
        })
        cell = Cell.objects.get(cell_id='C-1')
        self.assertTrue(CellHistory.objects.filter(cell=cell, change_type='created').exists())
        self.assertFalse(AuditLog.objects.filter(entity_type='Cell').exists())

    def test_finding_status_change_is_audited(self):
        session = DriveTestSession.objects.create(
            operator=self.op, test_date=TODAY, uploaded_by=self.admin, status='COMPLETED')
        finding = Finding.objects.create(
            session=session, finding_type='WEAK_SIGNAL', severity='HIGH', description='x')
        self.client.post(reverse('drive_test:finding_set_status', args=[finding.pk]), {'action': 'resolve'})
        row = AuditLog.objects.get(entity_type='Finding', entity_id=str(finding.pk))
        self.assertTrue(row.extra_data['changes']['is_resolved']['to'])

    def test_reference_import_is_audited(self):
        import io
        csv_content = 'region_code,region_name\nW,Western Area\n'
        self.client.post(reverse('drive_test:reference_import'), {
            'entity': 'regions', 'operator': str(self.op.pk),
            'csv_file': io.BytesIO(csv_content.encode()),
        }, format='multipart')
        self.assertTrue(AuditLog.objects.filter(entity_type='ReferenceImport').exists())

    def test_task_audits_processing_success_and_failure(self):
        session = DriveTestSession.objects.create(
            operator=self.op, test_date=TODAY, uploaded_by=self.admin, status='PROCESSING')
        f = DriveTestFile.objects.create(
            session=session, original_filename='a.trp', file_path='p', file_size=1, sha256='a' * 64,
            status='PARSING')
        dt_tasks._audit_processing_event(f, status='SUCCESS', measurements=5)
        row = AuditLog.objects.get(entity_type='DriveTestFile', entity_id=str(f.pk))
        self.assertEqual(row.extra_data['status'], 'SUCCESS')

        f2 = DriveTestFile.objects.create(
            session=session, original_filename='b.trp', file_path='p', file_size=1, sha256='b' * 64,
            status='FAILED', error_message=r'FileNotFoundError: not found: C:\x\y.trp')
        dt_tasks._audit_processing_event(f2, status='FAILED', error=f2.error_message)
        row2 = AuditLog.objects.get(entity_type='DriveTestFile', entity_id=str(f2.pk))
        self.assertEqual(row2.extra_data['status'], 'FAILED')
        self.assertNotIn('C:\\x', row2.extra_data['error'])


# ---------------------------------------------------------------------------
# The Audit Trail page itself
# ---------------------------------------------------------------------------

@STATIC
class AuditTrailPageTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('viewer', password='x')
        cls.admin = get_user_model().objects.create_user('radmin', password='x', is_regulatory_admin=True)
        cls.op = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.site = Site.objects.create(site_id='S1', name='Site One', operator=cls.op)
        cls.sector = Sector.objects.create(site=cls.site, sector_id='A')

        cls.rule_row = AuditLog.objects.create(
            user=cls.admin, action='CREATE', entity_type='RegulatoryRule', entity_id='1',
            description='Regulatory rule created: NATCA-1', extra_data={'fields': {'name': 'NATCA-1'}})
        cls.site_row = AuditLog.objects.create(
            user=cls.admin, action='UPDATE', entity_type='Site', entity_id=str(cls.site.pk),
            description='Site updated: S1',
            extra_data={'changes': {'name': {'from': 'Old', 'to': 'Site One'}}})
        cls.upload_row = AuditLog.objects.create(
            user=None, action='PROCESS', entity_type='DriveTestFile', entity_id='999',
            description='File processing failed: bad.trp', extra_data={'status': 'FAILED', 'error': 'boom'})

        cell = Cell.objects.create(cell_id='C-9', operator=cls.op, sector=cls.sector, technology='4G')
        CellHistory.objects.create(cell=cell, changed_by=cls.admin, change_type='created',
                                    snapshot={'cell_id': 'C-9', 'technology': '4G'})

        cls.url = reverse('drive_test:audit_trail')

    def setUp(self):
        self.client.force_login(self.user)

    def get(self, **q):
        return self.client.get(self.url, q)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 302)

    def test_page_loads_with_real_entries_merged_and_sorted(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        ids = [e.id for e in r.context['entries']]
        # 3 AuditLog rows + 1 CellHistory row, all real, none fabricated
        self.assertEqual(len(ids), 4)
        self.assertTrue(any(i.startswith('cell-') for i in ids))
        self.assertTrue(any(i.startswith('log-') for i in ids))

    def test_summary_counts_are_real(self):
        r = self.get()
        s = r.context['summary']
        self.assertEqual(s['total'], 4)
        self.assertEqual(s['config_changes'], 1)  # only the RegulatoryRule row

    def test_search_filter(self):
        r = self.get(q='NATCA-1')
        self.assertEqual(len(r.context['entries']), 1)

    def test_module_filter_network_reference_includes_site_and_cell(self):
        r = self.get(module='Network Reference')
        types = {e.entity_type for e in r.context['entries']}
        self.assertEqual(types, {'Site', 'Cell'})

    def test_action_filter(self):
        r = self.get(action='CREATE')
        types = {e.entity_type for e in r.context['entries']}
        self.assertEqual(types, {'RegulatoryRule', 'Cell'})  # both are real CREATE events

    def test_object_type_filter_cell(self):
        r = self.get(object_type='Cell')
        self.assertEqual(len(r.context['entries']), 1)
        self.assertEqual(r.context['entries'][0].source, 'cell')

    def test_invalid_module_reported_not_500(self):
        r = self.get(module='Not A Module')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Unrecognised module.', r.context['errors'])

    def test_status_column_shows_real_failed_status(self):
        r = self.get(q='bad.trp')
        self.assertContains(r, 'FAILED')

    def test_empty_state_when_filters_match_nothing(self):
        r = self.get(q='no-such-thing-anywhere')
        self.assertContains(r, 'No audit events match the selected filters.')

    def test_empty_state_when_no_events_at_all(self):
        AuditLog.objects.all().delete()
        CellHistory.objects.all().delete()
        r = self.get()
        self.assertContains(r, 'No audit events found')

    def test_page_is_read_only(self):
        r = self.get()
        self.assertNotContains(r, '>Edit<')
        self.assertNotContains(r, '>Delete<')

    # ── Detail page ──────────────────────────────────────────────────────────
    def test_detail_page_for_log_entry_shows_changes(self):
        r = self.client.get(reverse('drive_test:audit_detail', args=[f'log-{self.site_row.pk}']))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Old')
        self.assertContains(r, 'Site One')
        self.assertContains(r, 'Related Object')  # the real Site still exists -> a safe link

    def test_detail_page_for_missing_related_object_has_no_broken_link(self):
        r = self.client.get(reverse('drive_test:audit_detail', args=[f'log-{self.upload_row.pk}']))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['link'])  # DriveTestFile #999 does not exist -> never a broken link

    def test_detail_page_for_cell_history_entry(self):
        cell_entry_id = next(e.id for e in al.filter_entries({})[0] if e.source == 'cell')
        r = self.client.get(reverse('drive_test:audit_detail', args=[cell_entry_id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Related Object')

    def test_detail_page_404_for_unknown_entry(self):
        r = self.client.get(reverse('drive_test:audit_detail', args=['log-999999']))
        self.assertEqual(r.status_code, 404)
