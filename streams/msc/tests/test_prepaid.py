"""Unit tests for the CAMEL-IN-trigger prepaid rule.

Truth table for :func:`core.utils.prepaid.derive_msc_prepaid_flag` and
integration coverage for the MSC decoder helper.
"""
from django.test import TestCase

from core.utils.prepaid import derive_msc_prepaid_flag
from streams.msc.decoder import (
    _apply_prepaid_flag, parse_camel_voice_information, process_extended_tag,
    process_extended_tag_81,
)


class DerivePrepaidFlagTruthTableTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}

    def test_both_none_is_postpaid(self):
        self.assertEqual(derive_msc_prepaid_flag(None, None), 'POSTPAID')

    def test_both_empty_string_is_postpaid(self):
        self.assertEqual(derive_msc_prepaid_flag('', ''), 'POSTPAID')

    def test_whitespace_only_is_postpaid(self):
        self.assertEqual(derive_msc_prepaid_flag('  ', '\t '), 'POSTPAID')

    def test_zero_string_is_postpaid(self):
        # Upstream emits '0' to mean unset, not phase-zero.
        self.assertEqual(derive_msc_prepaid_flag('0', '0'), 'POSTPAID')

    def test_zero_int_is_postpaid(self):
        self.assertEqual(derive_msc_prepaid_flag(0, 0), 'POSTPAID')

    def test_service_key_only_is_prepaid(self):
        self.assertEqual(derive_msc_prepaid_flag('123', None), 'PREPAID')
        self.assertEqual(derive_msc_prepaid_flag('123', ''), 'PREPAID')

    def test_camel_phase_only_is_prepaid(self):
        self.assertEqual(derive_msc_prepaid_flag(None, '2'), 'PREPAID')
        self.assertEqual(derive_msc_prepaid_flag('', '4'), 'PREPAID')

    def test_both_populated_is_prepaid(self):
        self.assertEqual(derive_msc_prepaid_flag('123', '2'), 'PREPAID')

    def test_numeric_values_accepted(self):
        # int 1 and int 2 are real CAMEL phase values
        self.assertEqual(derive_msc_prepaid_flag(456, 2), 'PREPAID')

    def test_imsi_param_is_ignored(self):
        """IMSI is no longer used — pure CAMEL rule, regardless of IMSI block.
        Some prepaid subscribers do live in the '6190176100*' HLR range, so
        the IMSI block isn't a reliable postpaid marker."""
        # IMSI in old "postpaid block" + CAMEL → PREPAID (was POSTPAID under
        # the dropped IMSI-override rule)
        self.assertEqual(
            derive_msc_prepaid_flag('2', None, imsi='619017610089697'),
            'PREPAID',
        )
        # Same IMSI block, no CAMEL → POSTPAID (no IN trigger fired)
        self.assertEqual(
            derive_msc_prepaid_flag(None, None, imsi='619017610089697'),
            'POSTPAID',
        )
        # Outside old block, CAMEL fired → PREPAID
        self.assertEqual(
            derive_msc_prepaid_flag('2', None, imsi='619017650012345'),
            'PREPAID',
        )
        # IMSI not supplied at all — same result
        self.assertEqual(derive_msc_prepaid_flag('2', None), 'PREPAID')
        self.assertEqual(derive_msc_prepaid_flag(None, None), 'POSTPAID')


class DecoderApplyPrepaidFlagTests(TestCase):
    """``_apply_prepaid_flag`` writes '0'/'1' into the decoded record dict."""
    databases = {"default", "interconnect", "regulatory", "roaming"}

    def test_neither_field_present_writes_zero(self):
        rec = {'CHARGED_PARTY_IMSI': '619031234567890'}  # IMSI now irrelevant
        _apply_prepaid_flag(rec)
        self.assertEqual(rec['PREPAID_FLAG'], '0')

    def test_service_key_present_writes_one(self):
        rec = {'SERVICE_KEY': '15', 'CHARGED_PARTY_IMSI': '619031234567890'}
        _apply_prepaid_flag(rec)
        self.assertEqual(rec['PREPAID_FLAG'], '1')

    def test_camel_service_key_alias_also_works(self):
        # The decoder duplicates SERVICE_KEY into CAMEL_SERVICE_KEY;
        # either name is acceptable as the trigger signal.
        rec = {'CAMEL_SERVICE_KEY': '20'}
        _apply_prepaid_flag(rec)
        self.assertEqual(rec['PREPAID_FLAG'], '1')

    def test_camel_phase_only_writes_one(self):
        rec = {'CAMEL_PHASE': '2'}
        _apply_prepaid_flag(rec)
        self.assertEqual(rec['PREPAID_FLAG'], '1')

    def test_zero_string_treated_as_blank(self):
        # Decoder may emit literal '0' when binary tag was absent.
        rec = {'SERVICE_KEY': '0', 'CAMEL_PHASE': '0',
               'CHARGED_PARTY_IMSI': '619057999999999'}
        _apply_prepaid_flag(rec)
        self.assertEqual(rec['PREPAID_FLAG'], '0')

    def test_top_level_serviceKey_tag_0x9F_25(self):
        """Tag 0x9F 25 (serviceKey) is the authoritative prepaid signal."""
        rec = {}
        process_extended_tag(rec, 0x25, b'\x02')  # serviceKey = 2
        self.assertEqual(rec.get('SERVICE_KEY'), '2')
        self.assertEqual(rec.get('CAMEL_SERVICE_KEY'), '2')

    def test_calledServiceKey_tag_0x9F_86_05_does_NOT_set_SERVICE_KEY(self):
        """Tag 0x9F 86 05 (calledServiceKey) is the called party's IN
        service trigger — must NOT influence the CDR subject's prepaid flag.
        Lives in CALLED_SERVICE_KEY only."""
        rec = {}
        # ext1=0x86 → prefix=0x06, ext2=0x05 → inner_tag=0x05
        process_extended_tag_81(rec, 0x05, b'\x02', prefix=0x06)
        self.assertEqual(rec.get('CALLED_SERVICE_KEY'), '2')
        self.assertNotIn('SERVICE_KEY', rec)
        self.assertNotIn('CAMEL_SERVICE_KEY', rec)

    def test_BF20_originating_leg_sets_SERVICE_KEY(self):
        """BF20 (originating call leg) → CDR subject's CAMEL data → SERVICE_KEY."""
        rec = {}
        # cAMELCallLegInformation body: 0x80 0x01 0x02 (serviceKey=2)
        parse_camel_voice_information(rec, b'\x80\x01\x02', leg_tag=0x20)
        self.assertEqual(rec.get('SERVICE_KEY'), '2')
        self.assertEqual(rec.get('CAMEL_SERVICE_KEY'), '2')

    def test_BF21_terminating_leg_does_NOT_set_SERVICE_KEY(self):
        """BF21 (terminating leg) is the OTHER party's CAMEL data.
        Must not leak into the CDR subject's SERVICE_KEY — that was the
        '23276865513 misclassified as prepaid' bug."""
        rec = {}
        parse_camel_voice_information(rec, b'\x80\x01\x02', leg_tag=0x21)
        self.assertNotIn('SERVICE_KEY', rec)
        self.assertNotIn('CAMEL_SERVICE_KEY', rec)
        # Still recorded for traceability under leg-specific key
        self.assertEqual(rec.get('CAMEL_LEG_1_SERVICE_KEY'), '2')

    def test_BF22_forwarding_leg_does_NOT_set_SERVICE_KEY(self):
        rec = {}
        parse_camel_voice_information(rec, b'\x80\x01\x03', leg_tag=0x22)
        self.assertNotIn('SERVICE_KEY', rec)
        self.assertEqual(rec.get('CAMEL_LEG_2_SERVICE_KEY'), '3')

    def test_postpaid_caller_calling_prepaid_callee_classified_postpaid(self):
        """Full end-to-end: a postpaid caller (no BF20) whose call hits a
        prepaid callee (BF21 fires) must classify as POSTPAID."""
        rec = {}
        # Simulate: only the terminating leg has CAMEL (callee is prepaid)
        parse_camel_voice_information(rec, b'\x80\x01\x02', leg_tag=0x21)
        # No top-level serviceKey was present
        _apply_prepaid_flag(rec)
        self.assertEqual(rec['PREPAID_FLAG'], '0')  # POSTPAID

    def test_prepaid_caller_classified_prepaid(self):
        """Prepaid caller: BF20 fires (caller's CAMEL) → PREPAID."""
        rec = {}
        parse_camel_voice_information(rec, b'\x80\x01\x02', leg_tag=0x20)
        _apply_prepaid_flag(rec)
        self.assertEqual(rec['PREPAID_FLAG'], '1')

    def test_imsi_is_irrelevant_to_flag(self):
        """IMSI block is no longer authoritative — pure CAMEL rule.

        Some prepaid subscribers live in the '6190176100*' range too,
        so we rely on SERVICEKEY/CAMELPHASE only."""
        # IMSI in old "postpaid block" + CAMEL → PREPAID
        rec = {'CHARGED_PARTY_IMSI': '619017610089697', 'SERVICE_KEY': '2'}
        _apply_prepaid_flag(rec)
        self.assertEqual(rec['PREPAID_FLAG'], '1')

        # Same IMSI, no CAMEL → POSTPAID
        rec2 = {'CHARGED_PARTY_IMSI': '619017610089697'}
        _apply_prepaid_flag(rec2)
        self.assertEqual(rec2['PREPAID_FLAG'], '0')

        # Outside old block + CAMEL → PREPAID
        rec3 = {'CHARGED_PARTY_IMSI': '619017650012345', 'SERVICE_KEY': '2'}
        _apply_prepaid_flag(rec3)
        self.assertEqual(rec3['PREPAID_FLAG'], '1')
