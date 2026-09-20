from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from regulatory.models import Tariff, TariffComplianceResult
from regulatory.services.tariff_compliance import TariffComplianceService


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TariffComplianceServiceTests(TestCase):
    def make_tariff(self, *, rate, is_reference=False):
        return Tariff.objects.create(
            name='Voice On-Net', operator_code='orange', service_type=Tariff.ServiceType.VOICE,
            traffic_type=Tariff.TrafficType.ON_NET, subscriber_type=Tariff.SubscriberType.ALL,
            rate=rate, charging_unit=Tariff.ChargingUnit.PER_MINUTE,
            effective_from=date(2026, 1, 1), status=Tariff.Status.ACTIVE,
            is_regulatory_reference=is_reference,
        )

    def test_run_check_calculates_and_classifies_variance(self):
        self.make_tariff(rate=Decimal('1.000000'), is_reference=True)
        applied = self.make_tariff(rate=Decimal('1.025000'))

        summary = TariffComplianceService.run_check(effective_date=date(2026, 9, 13))

        result = TariffComplianceResult.objects.get(applied_tariff=applied)
        self.assertEqual(summary['total'], 1)
        self.assertEqual(result.variance, Decimal('0.025000'))
        self.assertEqual(result.variance_percent, Decimal('2.50'))
        self.assertEqual(result.status, TariffComplianceResult.Status.NON_COMPLIANT)

    def test_compliance_page_requires_login(self):
        response = self.client.get(reverse('regulatory:tariff_compliance_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_regulator_can_filter_results_and_export_csv(self):
        self.make_tariff(rate=Decimal('1.000000'), is_reference=True)
        self.make_tariff(rate=Decimal('1.025000'))
        TariffComplianceService.run_check(effective_date=date(2026, 9, 13))
        user = get_user_model().objects.create_user(username='regulator', password='test', is_regulator=True)
        self.client.force_login(user)

        response = self.client.get(reverse('regulatory:tariff_compliance_list'), {'search': 'Voice'})
        export = self.client.get(reverse('regulatory:tariff_compliance_export'), {'search': 'Voice'})

        self.assertContains(response, 'Voice On-Net')
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export['Content-Type'], 'text/csv')

    def test_admin_can_create_audit_finding_for_non_compliance(self):
        self.make_tariff(rate=Decimal('1.000000'), is_reference=True)
        self.make_tariff(rate=Decimal('1.025000'))
        TariffComplianceService.run_check(effective_date=date(2026, 9, 13))
        result = TariffComplianceResult.objects.get()
        user = get_user_model().objects.create_user(username='admin', password='test', is_regulatory_admin=True)
        self.client.force_login(user)

        response = self.client.post(
            reverse('regulatory:tariff_compliance_create_finding', args=[result.pk]),
            {'description': 'Applied rate is outside the configured tolerance.', 'severity': 'HIGH'},
        )

        result.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(result.finding_created)
        self.assertIsNotNone(result.finding_case)
