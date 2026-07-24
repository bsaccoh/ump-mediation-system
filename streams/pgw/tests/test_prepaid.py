"""Unit tests for the chargingCharacteristics → prepaid_flag rule.

Same rule applies to PGW, SGSN, and SGW — they all carry the standard
3GPP TS 32.298 chargingCharacteristics field with the P-flag at bit 3
of octet 1.
"""
from django.test import TestCase

from core.utils.prepaid import derive_prepaid_from_cc, derive_pgw_prepaid_flag


class DerivePgwPrepaidFlagTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}

    # --- POSTPAID outcomes -------------------------------------------------

    def test_blank_string_is_postpaid(self):
        self.assertEqual(derive_pgw_prepaid_flag(''), 'POSTPAID')

    def test_none_is_postpaid(self):
        self.assertEqual(derive_pgw_prepaid_flag(None), 'POSTPAID')

    def test_normal_profile_0x0800_is_postpaid(self):
        # 3GPP 'Normal' profile — typical postpaid CC value.
        self.assertEqual(derive_pgw_prepaid_flag('0800'), 'POSTPAID')

    def test_hot_billing_0x0100_is_postpaid(self):
        # Hot billing is not the same as prepaid.
        self.assertEqual(derive_pgw_prepaid_flag('0100'), 'POSTPAID')

    def test_flat_rate_0x0200_is_postpaid(self):
        self.assertEqual(derive_pgw_prepaid_flag('0200'), 'POSTPAID')

    def test_unparseable_value_is_postpaid(self):
        self.assertEqual(derive_pgw_prepaid_flag('not-hex'), 'POSTPAID')

    def test_zero_value_is_postpaid(self):
        self.assertEqual(derive_pgw_prepaid_flag('0000'), 'POSTPAID')

    # --- PREPAID outcomes --------------------------------------------------

    def test_prepaid_profile_0x0400_is_prepaid(self):
        # Canonical 3GPP Prepaid profile bit.
        self.assertEqual(derive_pgw_prepaid_flag('0400'), 'PREPAID')

    def test_prepaid_bit_set_with_other_bits_still_prepaid(self):
        # 0x0C00 = Prepaid + Normal — Prepaid bit dominates.
        self.assertEqual(derive_pgw_prepaid_flag('0C00'), 'PREPAID')

    def test_short_hex_without_leading_zeros_works(self):
        # '400' should parse as 0x400 = 1024 — Prepaid bit set.
        self.assertEqual(derive_pgw_prepaid_flag('400'), 'PREPAID')

    def test_hex_with_0x_prefix_works(self):
        self.assertEqual(derive_pgw_prepaid_flag('0x0400'), 'PREPAID')

    def test_uppercase_hex_works(self):
        self.assertEqual(derive_pgw_prepaid_flag('0F00'), 'PREPAID')

    def test_lowercase_hex_works(self):
        self.assertEqual(derive_pgw_prepaid_flag('0f00'), 'PREPAID')

    def test_integer_input_works(self):
        # Some upstreams may pass an int instead of a hex string.
        self.assertEqual(derive_pgw_prepaid_flag(0x0400), 'PREPAID')
        self.assertEqual(derive_pgw_prepaid_flag(0x0800), 'POSTPAID')

    # --- Single-byte CC encoding (some Huawei/other decoders emit one byte) -

    def test_single_byte_prepaid_0x04(self):
        # Just octet 1: bit 3 set = P flag = Prepaid
        self.assertEqual(derive_prepaid_from_cc('04'), 'PREPAID')

    def test_single_byte_normal_0x08_is_postpaid(self):
        self.assertEqual(derive_prepaid_from_cc('08'), 'POSTPAID')

    def test_single_byte_hot_billing_0x01_is_postpaid(self):
        self.assertEqual(derive_prepaid_from_cc('01'), 'POSTPAID')

    def test_single_byte_flat_rate_0x02_is_postpaid(self):
        self.assertEqual(derive_prepaid_from_cc('02'), 'POSTPAID')

    def test_single_byte_p_plus_n_is_prepaid(self):
        # 0x0C = 0b1100 = P + N both set; Prepaid wins.
        self.assertEqual(derive_prepaid_from_cc('0C'), 'PREPAID')

    def test_single_byte_int_works(self):
        self.assertEqual(derive_prepaid_from_cc(0x04), 'PREPAID')
        self.assertEqual(derive_prepaid_from_cc(0x08), 'POSTPAID')

    def test_back_compat_alias_works(self):
        # derive_pgw_prepaid_flag is an alias of derive_prepaid_from_cc.
        self.assertIs(derive_pgw_prepaid_flag, derive_prepaid_from_cc)
