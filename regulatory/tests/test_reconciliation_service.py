from decimal import Decimal

from django.test import SimpleTestCase

from regulatory.models import ReconciliationResult
from regulatory.services.reconciliation import ReconciliationService


class ReconciliationServiceTests(SimpleTestCase):
    def test_variance_and_tolerance_classification(self):
        amount, percent = ReconciliationService.variance(Decimal('100'), Decimal('94'))
        self.assertEqual(amount, Decimal('6'))
        self.assertEqual(percent, Decimal('6.00'))
        self.assertEqual(ReconciliationService.classify_variance(percent), ReconciliationResult.MatchStatus.MAJOR_VARIANCE)

    def test_zero_expected_amount_is_safe(self):
        amount, percent = ReconciliationService.variance(Decimal('0'), Decimal('5'))
        self.assertEqual(amount, Decimal('-5'))
        self.assertEqual(percent, Decimal('0'))
