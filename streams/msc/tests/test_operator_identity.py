"""DJ Phase 4 — per-operator home identity (de-hardcoded)."""
from django.test import TestCase

from core.operator_context import operator_context
from core.utils.operators import is_home_operator, home_operator_name
from reference.models import Operator
from streams.msc.decoder import home_identity, clear_home_identity_cache


class HomeIdentityTests(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901',
                                home_mcc='619', home_mnc='01')
        Operator.objects.create(code='africell', name='Africell SL', home_plmn='61902',
                                home_mcc='619', home_mnc='02')

    def setUp(self):
        clear_home_identity_cache()

    def test_home_identity_resolves_active_operator(self):
        with operator_context('orange'):
            self.assertEqual(home_identity(), ('61901', '619', '01'))
        with operator_context('africell'):
            self.assertEqual(home_identity(), ('61902', '619', '02'))

    def test_same_imsi_roams_for_one_operator_onnet_for_another(self):
        imsi = '61902' + '5550001'  # an Africell-PLMN IMSI
        with operator_context('orange'):
            plmn = home_identity()[0]
            self.assertFalse(imsi.startswith(plmn))   # foreign -> roaming for Orange
        with operator_context('africell'):
            plmn = home_identity()[0]
            self.assertTrue(imsi.startswith(plmn))     # on-net for Africell

    def test_fallback_to_orange_when_no_operator_row(self):
        with operator_context('qcell'):  # no Operator row created for qcell
            self.assertEqual(home_identity()[0], '61901')  # default fallback

    def test_is_home_operator_follows_active_operator(self):
        # 80 -> Qcell, 76 -> Orange in the national prefix map.
        with operator_context('qcell'):
            self.assertEqual(home_operator_name(), 'Qcell')
            self.assertTrue(is_home_operator('+23280123456'))
            self.assertFalse(is_home_operator('+23276123456'))
        with operator_context('orange'):
            self.assertTrue(is_home_operator('+23276123456'))
            self.assertFalse(is_home_operator('+23280123456'))
