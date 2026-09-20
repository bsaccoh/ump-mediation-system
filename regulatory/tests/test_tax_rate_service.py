from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from regulatory.models import TaxRate, TaxType
from regulatory.services.tax_rate_service import TaxRateService


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TaxRateServiceTests(TestCase):
    def setUp(self):
        self.tax_type = TaxType.objects.create(name='Goods and Services Tax', code='gst', category=TaxType.Category.GST)

    def test_new_version_preserves_history_and_selects_effective_rate(self):
        first = TaxRateService.add_rate_version(
            tax_type=self.tax_type, rate_percent=Decimal('15.000'), effective_from=date(2024, 1, 1),
        )
        second = TaxRateService.add_rate_version(
            tax_type=self.tax_type, rate_percent=Decimal('17.000'), effective_from=date(2027, 1, 1),
        )

        first.refresh_from_db()
        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(first.effective_to, date(2026, 12, 31))
        self.assertEqual(TaxRateService.find_effective_rate(self.tax_type, date(2026, 9, 13)).pk, first.pk)
        self.assertEqual(TaxRateService.find_effective_rate(self.tax_type, date(2027, 1, 1)).pk, second.pk)

    def test_tax_rate_list_requires_login(self):
        response = self.client.get(reverse('regulatory:tax_rate_list'))
        self.assertEqual(response.status_code, 302)

    def test_regulator_can_render_and_filter_tax_rates(self):
        TaxRateService.add_rate_version(
            tax_type=self.tax_type, rate_percent=Decimal('15.000'), effective_from=date(2024, 1, 1),
        )
        user = get_user_model().objects.create_user(username='regulator', password='test', is_regulator=True)
        self.client.force_login(user)

        response = self.client.get(reverse('regulatory:tax_rate_list'), {'category': 'GST', 'search': 'Goods'})

        self.assertContains(response, 'Goods and Services Tax')
        self.assertContains(response, '15.000')
