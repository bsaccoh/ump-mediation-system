"""Phase 7 tests: AI analyst evidence engine (deterministic, no fabrication).

Run: python manage.py test drive_test.tests.test_phase7 --settings=config.test_settings
"""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import Campaign, DriveTestFile, Project, Sample
from drive_test.services.ai_analyst import analyze
from drive_test.services.ai_llm import llm_enabled

User = get_user_model()


class AnalystMixin:
    def _campaign(self, rsrps):
        self.user = User.objects.create_user('ana', password='x', is_analyst=True)
        self.project = Project.objects.create(name='P', created_by=self.user)
        self.campaign = Campaign.objects.create(project=self.project, name='C', technology='LTE')
        self.dtf = DriveTestFile.objects.create(campaign=self.campaign, original_name='f.csv', sha256='7' * 64)
        base = datetime(2026, 9, 1, 10, 0, 0)
        Sample.objects.bulk_create([
            Sample(campaign=self.campaign, drive_file=self.dtf,
                   timestamp=base + timedelta(seconds=i), latitude=8.48 + i * 0.001,
                   longitude=-13.22 + i * 0.001, technology='LTE', rsrp=r, sinr=10.0)
            for i, r in enumerate(rsrps)
        ])


class EvidenceEngineTests(AnalystMixin, TestCase):
    def test_no_data_reports_gracefully(self):
        user = User.objects.create_user('x', password='x', is_analyst=True)
        p = Project.objects.create(name='P', created_by=user)
        c = Campaign.objects.create(project=p, name='Empty')
        result = analyze(c)
        self.assertFalse(result['has_data'])
        self.assertEqual(result['facts'], [])

    def test_poor_coverage_yields_cause_with_evidence(self):
        self._campaign([-115] * 40)  # all critical RSRP
        result = analyze(self.campaign, 'Why is performance poor?')
        self.assertTrue(result['has_data'])
        self.assertTrue(any('coverage' in c['cause'].lower() for c in result['causes']))
        # every cause carries evidence and a confidence label
        for c in result['causes']:
            self.assertTrue(c['evidence'])
            self.assertIn(c['confidence'], ['Low', 'Medium', 'High'])

    def test_facts_are_measured_not_fabricated(self):
        self._campaign([-85] * 30)
        result = analyze(self.campaign)
        samples_fact = next(f for f in result['facts'] if f['label'] == 'Samples')
        self.assertEqual(samples_fact['value'], 30)  # matches DB exactly

    def test_good_network_no_false_causes(self):
        self._campaign([-70] * 40)  # excellent RSRP, good SINR
        result = analyze(self.campaign)
        # No poor-coverage cause fabricated for a healthy network.
        self.assertFalse(any('coverage' in c['cause'].lower() for c in result['causes']))

    def test_llm_disabled_by_default(self):
        self.assertFalse(llm_enabled())


class AnalystPageTests(AnalystMixin, TestCase):
    def setUp(self):
        self._campaign([-115] * 30)
        self.client.force_login(self.user)

    def test_page_renders_without_campaign(self):
        self.assertEqual(self.client.get(reverse('drive_test:ai_analyst')).status_code, 200)

    def test_page_renders_with_analysis(self):
        resp = self.client.get(reverse('drive_test:ai_analyst'),
                               {'campaign': self.campaign.pk, 'question': 'Why poor?'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Measured Data')
        self.assertContains(resp, 'Recommended Investigation')

    @override_settings(DRIVE_TEST_AI_LLM_ENABLED=False)
    def test_narrative_absent_when_disabled(self):
        resp = self.client.get(reverse('drive_test:ai_analyst'), {'campaign': self.campaign.pk})
        self.assertIsNone(resp.context['narrative'])
