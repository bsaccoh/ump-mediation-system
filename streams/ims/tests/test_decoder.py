"""
Unit tests for the Huawei ATS9900 IMS BER decoder.

These tests lock in every tag mapping and arithmetic fix discovered during
field-testing so they can't silently regress.  Run with:

    python manage.py test streams.ims.tests
"""
from django.test import TestCase

from streams.ims import decoder as D


# ---------------------------------------------------------------------------
# Helpers used in several tests
# ---------------------------------------------------------------------------

def _build_record_body(*tlvs: bytes) -> bytes:
    """Concatenate raw TLV bytes into a synthetic record body."""
    return b''.join(tlvs)


def _tlv(tag: int, value: bytes) -> bytes:
    """Encode a single-byte BER TLV (length always short-form)."""
    return bytes([tag, len(value)]) + value


# ---------------------------------------------------------------------------
# Low-level BER primitives
# ---------------------------------------------------------------------------

class ReadTagTests(TestCase):
    """Verify the single-byte vs multi-byte tag arithmetic."""

    def test_single_byte_context_primitive(self):
        # 0x96 (context [22] primitive) — used for online_charging_flag
        tag, consumed, constructed = D._read_tag(b'\x96\x01\x00', 0)
        self.assertEqual(tag, 0x96)
        self.assertEqual(consumed, 1)
        self.assertFalse(constructed)

    def test_single_byte_context_constructed(self):
        # 0xA6 (context [6] constructed) — list-Of-Calling-Party-Address
        tag, consumed, constructed = D._read_tag(b'\xA6\x0E', 0)
        self.assertEqual(tag, 0xA6)
        self.assertTrue(constructed)

    def test_multi_byte_9F_81_xx(self):
        # 0x9F 0x81 0x48 → tag_num = (1<<7) | 0x48 = 200 (duration_raw)
        tag, consumed, constructed = D._read_tag(b'\x9F\x81\x48', 0)
        self.assertEqual(tag, 200)
        self.assertEqual(consumed, 3)
        self.assertFalse(constructed)

    def test_multi_byte_9F_81_4C(self):
        # 0x9F 0x81 0x4C → 204 (ringing_duration_raw) — verifies arithmetic fix
        tag, _, _ = D._read_tag(b'\x9F\x81\x4C', 0)
        self.assertEqual(tag, 204)

    def test_multi_byte_BF_83_22(self):
        # 0xBF 0x83 0x22 → 418 (Private-User-Equipment-Info constructed)
        tag, _, constructed = D._read_tag(b'\xBF\x83\x22', 0)
        self.assertEqual(tag, 418)
        self.assertTrue(constructed)

    def test_multi_byte_BF_1F(self):
        # 0xBF 0x1F → 31 (list-Of-Subscription-ID — Huawei prod)
        tag, _, constructed = D._read_tag(b'\xBF\x1F', 0)
        self.assertEqual(tag, 31)
        self.assertTrue(constructed)


# ---------------------------------------------------------------------------
# Timestamp decoder
# ---------------------------------------------------------------------------

class TimestampTests(TestCase):
    def test_2026_05_01_115530(self):
        # Huawei format: yy mm dd hh mm ss tz_msb tz_lsb _
        ts = D._decode_timestamp(b'\x26\x05\x01\x11\x55\x30\x00\x00\x00')
        self.assertIsNotNone(ts)
        self.assertEqual(ts.year, 2026)
        self.assertEqual(ts.month, 5)
        self.assertEqual(ts.day, 1)
        self.assertEqual(ts.hour, 11)
        self.assertEqual(ts.minute, 55)
        self.assertEqual(ts.second, 30)

    def test_year_69_means_1969(self):
        # Sanity: BCD year ≥ 70 wraps to 1900s, < 70 wraps to 2000s
        ts = D._decode_timestamp(b'\x69\x01\x01\x00\x00\x00\x00\x00\x00')
        self.assertEqual(ts.year, 2069)
        ts = D._decode_timestamp(b'\x70\x01\x01\x00\x00\x00\x00\x00\x00')
        self.assertEqual(ts.year, 1970)


# ---------------------------------------------------------------------------
# Access-Network-Info parser
# ---------------------------------------------------------------------------

class AccessNetworkInfoTests(TestCase):
    def test_eutran_cell_id(self):
        out = D.parse_access_network_info(
            '3GPP-E-UTRAN;utran-cell-id-3gpp=619011016010E715;"ue-ip=10.10.26.19"'
        )
        self.assertEqual(out['technology'],   'EUTRAN')
        self.assertEqual(out['serving_plmn'], '61901')
        self.assertEqual(out['tac'],          '4118')        # 0x1016 decimal
        self.assertEqual(out['cell_id'],      '1107733')     # 0x010E715 decimal
        self.assertEqual(out['enodeb_id'],    '4327')        # ECI >> 8
        self.assertEqual(out['ue_ip'],        '10.10.26.19')

    def test_utran_sai_decoding(self):
        # 13-hex SAI value — was previously broken (only LAC was set)
        out = D.parse_access_network_info(
            '3GPP-UTRAN-FDD;utran-sai-3gpp=6190175A482D6;network-provided'
        )
        self.assertEqual(out['technology'],   'UTRAN')
        self.assertEqual(out['serving_plmn'], '61901')
        self.assertEqual(out['lac'],          '30116')       # 0x75A4 decimal
        self.assertEqual(out['cell_id'],      '33494')       # 0x82D6 decimal — SAC

    def test_geran_cgi(self):
        out = D.parse_access_network_info(
            '3GPP-GERAN;cgi-3gpp=619012710ABCD;network-provided'
        )
        self.assertEqual(out['technology'],   'GERAN')
        self.assertEqual(out['serving_plmn'], '61901')
        self.assertEqual(out['lac'],          '10000')       # 0x2710
        self.assertEqual(out['cell_id'],      '43981')       # 0xABCD


# ---------------------------------------------------------------------------
# SDP parser
# ---------------------------------------------------------------------------

class SdpParserTests(TestCase):
    SAMPLE = (
        'm=audio 28818 RTP/AVP 104 100 '
        'a=rtpmap:104 AMR-WB/16000 '
        'a=fmtp:104 mode-set=0,1,2 '
        'a=rtpmap:100 telephone-event/8000 '
        'a=ptime:20'
    )

    def test_parses_audio_media(self):
        out = D.parse_sdp_block(self.SAMPLE)
        self.assertEqual(out['media_type'],              'audio')
        self.assertEqual(out['rtp_port'],                '28818')
        self.assertEqual(out['rtp_protocol'],            'RTP/AVP')
        self.assertEqual(out['sip_codec_payload'],       '104')
        self.assertEqual(out['telephone_event_payload'], '100')
        self.assertEqual(out['codec'],                   'AMR-WB/16000')

    def test_empty_input_safe(self):
        self.assertEqual(D.parse_sdp_block(''), {})
        self.assertEqual(D.parse_sdp_block(None), {})


# ---------------------------------------------------------------------------
# BCD telephony address (SCF-Address)
# ---------------------------------------------------------------------------

class TelephonyAddressTests(TestCase):
    def test_bcd_decode_scf_address(self):
        # 0x23 0x27 0x60 0x20 0x08 0x6F  →  '23276020086' (F = filler)
        out = D._decode_telephony_address(b'\x23\x27\x60\x20\x08\x6F')
        self.assertEqual(out, '23276020086')

    def test_ascii_passthrough(self):
        # ASCII '23276020090' → returned as-is
        out = D._decode_telephony_address(b'23276020090')
        self.assertEqual(out, '23276020090')

    def test_empty(self):
        self.assertEqual(D._decode_telephony_address(b''), '')


# ---------------------------------------------------------------------------
# String control-character stripping
# ---------------------------------------------------------------------------

class DecodeStringTests(TestCase):
    def test_strips_control_chars(self):
        # Embedded \r\n must be replaced with spaces (Excel CSV-row-break fix)
        s = D._decode_string(b'ftpcscf01.194.52\r\n.20260513165930')
        self.assertNotIn('\r', s)
        self.assertNotIn('\n', s)
        self.assertEqual(s, 'ftpcscf01.194.52 .20260513165930')

    def test_collapses_whitespace(self):
        s = D._decode_string(b'foo   \t  bar')
        self.assertEqual(s, 'foo bar')


# ---------------------------------------------------------------------------
# IMSI extraction from List-Of-Subscription-ID
# ---------------------------------------------------------------------------

class SubscriptionIdTests(TestCase):
    def test_extracts_imsi_from_set_wrapper(self):
        # Outer wrapper: universal SET (0x31), inner: type=1 (IMSI) + data
        inner = _tlv(0x80, bytes([1])) + _tlv(0x81, b'619017648626660')
        set_block = bytes([0x31, len(inner)]) + inner
        rec = {}
        D._handle_subscription_id_list(set_block, rec)
        self.assertEqual(rec.get('imsi'), '619017648626660')

    def test_extracts_imsi_directly(self):
        # No universal-SET wrapper — sub-tags are at top level
        inner = _tlv(0x80, bytes([1])) + _tlv(0x81, b'619017648626660')
        rec = {}
        D._handle_subscription_id_list(inner, rec)
        self.assertEqual(rec.get('imsi'), '619017648626660')


# ---------------------------------------------------------------------------
# Private-User-Equipment-Info (IMEI)
# ---------------------------------------------------------------------------

class PrivateUserEquipmentInfoTests(TestCase):
    def test_extracts_imei_digits_only(self):
        # Type 0 = IMEI, value with dashes
        block = _tlv(0x80, bytes([0])) + _tlv(0x81, b'35125339-616639-0')
        rec = {}
        D._handle_private_user_equipment_info(block, rec)
        self.assertEqual(rec['imei'], '351253396166390')
        self.assertEqual(rec['private_user_equipment_info_type'], 'IMEI')
        self.assertEqual(rec['private_user_equipment_info_value'], '35125339-616639-0')

    def test_esn_type(self):
        block = _tlv(0x80, bytes([1])) + _tlv(0x81, b'A1B2C3D4')
        rec = {}
        D._handle_private_user_equipment_info(block, rec)
        self.assertEqual(rec['private_user_equipment_info_type'], 'ESN')
        # ESN doesn't fill the imei field
        self.assertNotIn('imei', rec)


# ---------------------------------------------------------------------------
# Record-type & enum mappings
# ---------------------------------------------------------------------------

class EnumMappingTests(TestCase):
    def test_record_type_69_is_as_cdr(self):
        self.assertEqual(D._map_record_type(69), 'AS_CDR')

    def test_record_type_63_is_scscf(self):
        self.assertEqual(D._map_record_type(63), 'SCSCF_CDR')

    def test_cause_for_closing_0(self):
        self.assertEqual(D.CAUSE_FOR_CLOSING[0], 'SERVICE_DELIVERY_END_SUCCESSFULLY')

    def test_charging_category_normal_huawei_mnemonic(self):
        # User explicitly wanted CHARGE_NORMAL (not just NORMAL)
        # via _EXT_TAG_FIELDS[216] → 'charging_category_raw' → enum lookup
        self.assertEqual(D._CHARGING_CATEGORY[0], 'CHARGE_NORMAL')
        self.assertEqual(D._CHARGING_CATEGORY[5], 'CHARGE_POSTPAID')

    def test_call_property_huawei_mnemonics(self):
        self.assertEqual(D._CALL_PROPERTY[0], 'Unknown-Call')
        self.assertEqual(D._CALL_PROPERTY[1], 'Local-Call')

    def test_online_charging_flag_mnemonic(self):
        self.assertEqual(D.ONLINE_CHARGING_FLAG[0], 'Offline_Charging')
        self.assertEqual(D.ONLINE_CHARGING_FLAG[1], 'Online_Charging')

    def test_supplementary_service_oip(self):
        self.assertEqual(D.MMTEL_SUPPLEMENTARY_SERVICE[0], 'OIP')
        self.assertEqual(D.MMTEL_SUPPLEMENTARY_SERVICE[7], 'CDIV')
