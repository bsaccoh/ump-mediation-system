"""DJ Phase 1 — filename classification into (operator, vendor, NE, decoder)."""
from django.test import TestCase

from core.enums import DecoderType
from collection.services.file_detector import classify_file, detect_decoder_type
from reference.models import Operator, SourcePattern


class ClassifyFileTests(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        cls.orange = Operator.objects.create(
            code='orange', name='Orange SL', home_plmn='61901',
            home_mcc='619', home_mnc='01',
        )
        cls.africell = Operator.objects.create(
            code='africell', name='Africell SL', home_plmn='61902',
            home_mcc='619', home_mnc='02',
        )
        SourcePattern.objects.create(
            pattern='ftmsx', operator=cls.orange, vendor='huawei',
            network_element='msc', decoder_type=DecoderType.MSC, priority=10,
        )
        # Africell pattern + a regex example; higher priority number = later.
        SourcePattern.objects.create(
            pattern=r'^afc.*\.dat$', is_regex=True, operator=cls.africell,
            vendor='huawei', network_element='msc',
            decoder_type=DecoderType.AUTO, priority=20,
        )

    def test_orange_msc_match(self):
        r = classify_file('bFTMSX01001273367467120260317.dat')
        self.assertEqual(r.operator, 'orange')
        self.assertEqual(r.vendor, 'huawei')
        self.assertEqual(r.network_element, 'msc')
        self.assertEqual(r.decoder_type, DecoderType.MSC)

    def test_regex_match_with_auto_decoder_falls_back(self):
        r = classify_file('AFC_voice_20260317.dat')
        self.assertEqual(r.operator, 'africell')
        # decoder_type AUTO -> derived from extension (.dat => MSC)
        self.assertEqual(r.decoder_type, DecoderType.MSC)

    def test_no_match_returns_decoder_only(self):
        r = classify_file('something_unknown.pgw')  # 'pgw' substring -> PGW decoder
        self.assertIsNone(r.operator)
        self.assertIsNone(r.vendor)
        self.assertEqual(r.decoder_type, detect_decoder_type('something_unknown.pgw'))

    def test_priority_first_match_wins(self):
        # Add a broad high-priority (low number) pattern that should win.
        SourcePattern.objects.create(
            pattern='msx', operator=self.africell, vendor='huawei',
            network_element='msc', decoder_type=DecoderType.MSC, priority=1,
        )
        r = classify_file('bFTMSX01_x.dat')
        self.assertEqual(r.operator, 'africell')  # priority 1 beats orange's 10

    def test_disabled_pattern_ignored(self):
        SourcePattern.objects.filter(pattern='ftmsx').update(enabled=False)
        r = classify_file('bFTMSX01_x.dat')
        self.assertIsNone(r.operator)
