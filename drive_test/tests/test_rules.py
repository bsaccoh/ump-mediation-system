"""Rules & Thresholds Management workspace tests."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    DriveTestFile, DriveTestSession, Finding, RegulatoryRule, RegulatoryThreshold,
)
from drive_test.services import rules as rl
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})

TODAY = date.today()


@STATIC
class RulesThresholdsTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.user = U.objects.create_user('viewer', password='x')
        cls.admin = U.objects.create_superuser('admin', 'admin@example.com', 'x')
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')

        # ── Active rule (in effective window, is_active=True) ───────────────────
        cls.rule_active = RegulatoryRule.objects.create(
            rule_code='NATCA-RSRP-4G-001', name='LTE RSRP Minimum', description='Minimum acceptable RSRP on LTE.',
            technology='4G', service_type='', metric='rsrp', condition='lt',
            authority='NATCA', regulatory_reference='NATCA-2026-01', is_active=True,
            effective_from=TODAY - timedelta(days=30),
        )
        # ── Inactive rule ────────────────────────────────────────────────────────
        cls.rule_inactive = RegulatoryRule.objects.create(
            rule_code='NATCA-CSSR-001', name='Call Setup Success Rate', metric='call_setup_time_ms',
            condition='gt', is_active=False, effective_from=TODAY - timedelta(days=30),
        )
        # ── Scheduled rule (starts in the future) ───────────────────────────────
        cls.rule_scheduled = RegulatoryRule.objects.create(
            rule_code='NATCA-MOS-001', name='Voice MOS Minimum', metric='mos', condition='lt',
            is_active=True, effective_from=TODAY + timedelta(days=30),
        )
        # ── Expired rule ─────────────────────────────────────────────────────────
        cls.rule_expired = RegulatoryRule.objects.create(
            rule_code='NATCA-OLD-001', name='Old Rule', metric='sinr', condition='lt',
            is_active=True, effective_from=TODAY - timedelta(days=400), effective_to=TODAY - timedelta(days=10),
        )

        cls.threshold_active = RegulatoryThreshold.objects.create(
            rule=cls.rule_active, operator=cls.orange, warning_value=-95.0, critical_value=-100.0,
            unit='dBm', effective_from=TODAY - timedelta(days=30),
        )
        cls.threshold_all_ops = RegulatoryThreshold.objects.create(
            rule=cls.rule_active, operator=None, critical_value=-105.0,
            unit='dBm', effective_from=TODAY - timedelta(days=30),
        )
        cls.threshold_expired = RegulatoryThreshold.objects.create(
            rule=cls.rule_expired, critical_value=0.0, unit='dB',
            effective_from=TODAY - timedelta(days=400), effective_to=TODAY - timedelta(days=10),
        )

        # ── A real session/finding wired to threshold_active, for usage counts ──
        cls.session = DriveTestSession.objects.create(
            operator=cls.orange, test_date=TODAY, uploaded_by=cls.user, status='COMPLETED')
        DriveTestFile.objects.create(
            session=cls.session, original_filename='a.trp', file_path='p', file_size=1, sha256='a' * 64,
            status='COMPLETED')
        cls.finding = Finding.objects.create(
            session=cls.session, finding_type='WEAK_SIGNAL', severity='HIGH',
            description='LTE RSRP Minimum: observed rsrp=-102.00dBm (lt -100.0dBm)',
            measured_value=-102.0, threshold_value=-100.0, threshold=cls.threshold_active,
        )

        cls.url = reverse('drive_test:rules_thresholds')

    def setUp(self):
        self.client.force_login(self.user)

    def get(self, **q):
        return self.client.get(self.url, q)

    # ── Access ───────────────────────────────────────────────────────────────
    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 302)

    # ── Status derivation (services.rules) ──────────────────────────────────
    def test_rule_status_derivation(self):
        self.assertEqual(rl.rule_status(self.rule_active), rl.ACTIVE)
        self.assertEqual(rl.rule_status(self.rule_inactive), rl.INACTIVE)
        self.assertEqual(rl.rule_status(self.rule_scheduled), rl.SCHEDULED)
        self.assertEqual(rl.rule_status(self.rule_expired), rl.EXPIRED)

    def test_threshold_status_derivation(self):
        self.assertEqual(rl.threshold_status(self.threshold_active), rl.ACTIVE)
        self.assertEqual(rl.threshold_status(self.threshold_expired), rl.EXPIRED)

    # ── Rules tab ────────────────────────────────────────────────────────────
    def test_rules_tab_shows_real_data_and_derived_status(self):
        r = self.get(tab='rules')
        self.assertEqual(r.status_code, 200)
        rows = {x.pk: x for x in r.context['rules']}
        self.assertEqual(rows[self.rule_active.pk].status, 'active')
        self.assertEqual(rows[self.rule_inactive.pk].status, 'inactive')
        self.assertEqual(rows[self.rule_scheduled.pk].status, 'scheduled')
        self.assertEqual(rows[self.rule_expired.pk].status, 'expired')
        self.assertEqual(rows[self.rule_active.pk].threshold_count, 2)
        self.assertEqual(rows[self.rule_active.pk].findings_count, 1)
        self.assertEqual(rows[self.rule_inactive.pk].findings_count, 0)

    def test_rules_filter_by_status(self):
        r = self.get(tab='rules', status='inactive')
        self.assertEqual([x.pk for x in r.context['rules']], [self.rule_inactive.pk])

    def test_rules_filter_by_technology(self):
        r = self.get(tab='rules', technology='4G')
        self.assertEqual([x.pk for x in r.context['rules']], [self.rule_active.pk])

    def test_rules_search_filter(self):
        r = self.get(tab='rules', q='MOS')
        self.assertEqual([x.pk for x in r.context['rules']], [self.rule_scheduled.pk])

    def test_rules_invalid_status_reported_not_500(self):
        r = self.get(tab='rules', status='bogus')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Unrecognised status.', r.context['rule_errors'])

    # ── Thresholds tab ───────────────────────────────────────────────────────
    def test_thresholds_tab_shows_real_data(self):
        r = self.get(tab='thresholds')
        rows = {x.pk: x for x in r.context['thresholds']}
        self.assertEqual(rows[self.threshold_active.pk].operator.code, 'orange')
        self.assertEqual(rows[self.threshold_all_ops.pk].operator, None)
        self.assertEqual(rows[self.threshold_active.pk].findings_count, 1)
        self.assertEqual(rows[self.threshold_all_ops.pk].findings_count, 0)

    def test_thresholds_filter_by_operator(self):
        r = self.get(tab='thresholds', operator='orange')
        self.assertEqual([x.pk for x in r.context['thresholds']], [self.threshold_active.pk])

    def test_thresholds_status_choices_exclude_inactive(self):
        r = self.get(tab='thresholds')
        self.assertNotIn('inactive', dict(r.context['threshold_status_choices']))

    # ── Regulatory safety: no hard-coded judgement words ────────────────────
    def test_no_hardcoded_compliance_language(self):
        r = self.get()
        for word in ('PASS', 'FAIL', 'COMPLIANT', 'NON-COMPLIANT'):
            self.assertNotContains(r, word)

    # ── Empty states ─────────────────────────────────────────────────────────
    def test_empty_state_when_no_configuration_at_all(self):
        RegulatoryThreshold.objects.all().delete()
        RegulatoryRule.objects.all().delete()
        r = self.get()
        self.assertContains(r, 'No regulatory rules configured')
        self.assertContains(r, 'Regulatory analysis configuration is not currently available.')

    def test_empty_state_filters_match_nothing(self):
        r = self.get(tab='rules', q='does-not-exist-anywhere')
        self.assertContains(r, 'No regulatory rules match your filters.')

    # ── Rule detail ──────────────────────────────────────────────────────────
    def test_rule_detail_shows_thresholds_and_findings(self):
        r = self.client.get(reverse('drive_test:rule_detail', args=[self.rule_active.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['findings_count'], 1)
        self.assertEqual(len(r.context['thresholds']), 2)
        self.assertContains(r, 'LTE RSRP Minimum')

    def test_rule_detail_no_findings_message(self):
        # A real, accurately-computed zero count in the toolbar ("0 findings") is fine —
        # it's the descriptive empty-state body text that must never be skipped in favour
        # of a bare zero (spec section 16). Both appear together here.
        r = self.client.get(reverse('drive_test:rule_detail', args=[self.rule_inactive.pk]))
        self.assertEqual(r.context['findings_count'], 0)
        self.assertContains(r, 'No findings generated from this configuration.')

    # ── Threshold detail ─────────────────────────────────────────────────────
    def test_threshold_detail_shows_rule_scope(self):
        r = self.client.get(reverse('drive_test:threshold_detail', args=[self.threshold_active.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'RSRP')
        self.assertEqual(r.context['findings_count'], 1)

    def test_threshold_detail_hides_expire_when_already_expired(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('drive_test:threshold_detail', args=[self.threshold_expired.pk]))
        self.assertNotContains(r, '>Expire<')

    # ── Permissions ──────────────────────────────────────────────────────────
    def test_create_edit_requires_admin(self):
        r = self.client.get(reverse('drive_test:rule_create'))
        self.assertEqual(r.status_code, 302)  # redirected (not authorised)
        r = self.client.get(reverse('drive_test:threshold_create'))
        self.assertEqual(r.status_code, 302)

    def test_admin_can_reach_create_forms(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('drive_test:rule_create')).status_code, 200)
        self.assertEqual(self.client.get(reverse('drive_test:threshold_create')).status_code, 200)

    def test_viewer_does_not_see_edit_links(self):
        r = self.get()
        self.assertNotContains(r, 'Add Rule')
        self.assertNotContains(r, 'Add Threshold')

    def test_admin_sees_edit_links(self):
        self.client.force_login(self.admin)
        r = self.get()
        self.assertContains(r, 'Add Rule')
        self.assertContains(r, 'Add Threshold')

    # ── Toggle / expire (safe alternative to delete) ────────────────────────
    def test_rule_toggle_active_requires_post(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('drive_test:rule_toggle_active', args=[self.rule_active.pk]))
        self.assertEqual(r.status_code, 405)

    def test_rule_toggle_active_flips_flag(self):
        self.client.force_login(self.admin)
        self.assertTrue(self.rule_active.is_active)
        self.client.post(reverse('drive_test:rule_toggle_active', args=[self.rule_active.pk]))
        self.rule_active.refresh_from_db()
        self.assertFalse(self.rule_active.is_active)

    def test_rule_toggle_requires_admin(self):
        r = self.client.post(reverse('drive_test:rule_toggle_active', args=[self.rule_active.pk]))
        self.assertEqual(r.status_code, 302)
        self.rule_active.refresh_from_db()
        self.assertTrue(self.rule_active.is_active)  # unchanged

    def test_threshold_expire_sets_effective_to_today(self):
        self.client.force_login(self.admin)
        self.assertIsNone(self.threshold_active.effective_to)
        self.client.post(reverse('drive_test:threshold_expire', args=[self.threshold_active.pk]))
        self.threshold_active.refresh_from_db()
        self.assertEqual(self.threshold_active.effective_to, TODAY)

    def test_threshold_expire_noop_when_already_expired(self):
        self.client.force_login(self.admin)
        original = self.threshold_expired.effective_to
        self.client.post(reverse('drive_test:threshold_expire', args=[self.threshold_expired.pk]))
        self.threshold_expired.refresh_from_db()
        self.assertEqual(self.threshold_expired.effective_to, original)  # never un-expires or shortens

    # ── Forms / validation ───────────────────────────────────────────────────
    def test_rule_form_rejects_effective_from_after_effective_to(self):
        from drive_test.forms import RegulatoryRuleForm
        form = RegulatoryRuleForm(data={
            'rule_code': 'X-1', 'name': 'X', 'description': '', 'technology': '',
            'service_type': '', 'metric': 'rsrp', 'condition': 'lt', 'regulatory_reference': '',
            'authority': '', 'is_active': True,
            'effective_from': TODAY.isoformat(), 'effective_to': (TODAY - timedelta(days=1)).isoformat(),
        })
        self.assertFalse(form.is_valid())

    def test_rule_form_rejects_unknown_metric(self):
        from drive_test.forms import RegulatoryRuleForm
        form = RegulatoryRuleForm(data={
            'rule_code': 'X-2', 'name': 'X', 'metric': 'not_a_real_metric', 'condition': 'lt',
            'is_active': True, 'effective_from': TODAY.isoformat(),
        })
        self.assertFalse(form.is_valid())
        self.assertIn('metric', form.errors)

    def test_threshold_form_rejects_inconsistent_warning_for_lt_condition(self):
        from drive_test.forms import RegulatoryThresholdForm
        form = RegulatoryThresholdForm(data={
            'rule': self.rule_active.pk, 'operator': '', 'warning_value': -110.0,
            'critical_value': -100.0, 'unit': 'dBm', 'effective_from': TODAY.isoformat(),
        })
        # rule_active's condition is 'lt' -> warning must be > critical, not <
        self.assertFalse(form.is_valid())
        self.assertIn('warning_value', form.errors)

    def test_threshold_form_accepts_consistent_warning_for_lt_condition(self):
        from drive_test.forms import RegulatoryThresholdForm
        form = RegulatoryThresholdForm(data={
            'rule': self.rule_active.pk, 'operator': '', 'warning_value': -95.0,
            'critical_value': -100.0, 'unit': 'dBm', 'effective_from': TODAY.isoformat(),
        })
        self.assertTrue(form.is_valid(), form.errors)
