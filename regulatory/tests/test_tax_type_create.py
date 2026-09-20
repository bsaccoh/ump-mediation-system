from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditLog
from regulatory.models import TaxRate, TaxType
from regulatory.services.tax_rate_service import TaxRateService, TaxRateValidationError


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TaxTypeCreateTests(TestCase):
    def test_creates_normalized_tax_type_and_initial_version(self):
        tax_type, rate = TaxRateService.create_tax_type_with_initial_rate(
            name='Goods and Services Tax', code='gst', category=TaxType.Category.GST,
            description='Configurable GST.', rate_percent=Decimal('15.000'),
            effective_from=date.today(), status=TaxRate.Status.ACTIVE,
        )

        self.assertEqual(tax_type.code, 'GST')
        self.assertEqual(rate.version, 1)
        self.assertEqual(rate.status, TaxRate.Status.ACTIVE)
        self.assertTrue(AuditLog.objects.filter(entity_id=str(tax_type.pk), entity_type='TaxType').exists())
        self.assertTrue(AuditLog.objects.filter(entity_id=str(rate.pk), entity_type='TaxRate').exists())

    def test_duplicate_code_is_case_insensitive_and_transaction_rolls_back(self):
        TaxType.objects.create(name='Goods and Services Tax', code='GST', category=TaxType.Category.GST)

        with self.assertRaises(TaxRateValidationError) as error:
            TaxRateService.create_tax_type_with_initial_rate(
                name='Duplicate GST', code='gst', category=TaxType.Category.GST,
                description='', rate_percent='15', effective_from=date.today(), status=TaxRate.Status.DRAFT,
            )

        self.assertIn('code', error.exception.field_errors)
        self.assertEqual(TaxType.objects.count(), 1)
        self.assertEqual(TaxRate.objects.count(), 0)

    def test_future_active_rate_becomes_scheduled_and_effective_on_its_start_date(self):
        tax_type, rate = TaxRateService.create_tax_type_with_initial_rate(
            name='Universal Service Levy', code='usl', category=TaxType.Category.LEVY,
            description='', rate_percent='1.5', effective_from=date.today() + timedelta(days=5),
            status=TaxRate.Status.ACTIVE,
        )

        self.assertEqual(rate.status, TaxRate.Status.SCHEDULED)
        self.assertEqual(
            TaxRateService.find_effective_rate(tax_type, date.today() + timedelta(days=5)),
            rate,
        )

    def test_admin_can_open_and_submit_create_page(self):
        user = get_user_model().objects.create_user(username='taxadmin', password='test', is_regulatory_admin=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('regulatory:tax_type_create')).status_code, 200)

        response = self.client.post(reverse('regulatory:tax_type_create'), {
            'name': 'Regulatory Fee', 'code': 'reg', 'category': TaxType.Category.REGULATORY,
            'description': '', 'rate_percent': '2.000', 'effective_from': str(date.today()),
            'effective_to': '', 'status': TaxRate.Status.DRAFT,
        })

        self.assertRedirects(response, reverse('regulatory:tax_rate_list'))
        self.assertTrue(TaxRate.objects.filter(tax_type__code='REG', version=1).exists())
