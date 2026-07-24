"""DJ Phase 2 — per-operator directory layout (output path + filename)."""
import os

from django.conf import settings
from django.test import TestCase

from core.dispatcher import _build_filename
from portals.models import OutputPortal


class _FakeCDRFile:
    def __init__(self, filename, pk=1):
        self.filename = filename
        self.pk = pk


class ResolveDirectoryTests(TestCase):
    databases = {'default'}

    @staticmethod
    def _norm(p):
        return p.replace('\\', '/')

    def test_default_template_is_operator_output_vendor_ne(self):
        portal = OutputPortal(name='BIGDATA', portal_type='LOCAL',
                              output_format='CSV', stream_type='MSC', directory='')
        d = portal.resolve_directory(operator='orange', vendor='huawei',
                                     network_element='msc')
        self.assertTrue(self._norm(d).endswith('orange/output/huawei/msc'), d)
        self.assertTrue(d.startswith(str(settings.DATA_DIR)))

    def test_placeholders_in_explicit_directory(self):
        portal = OutputPortal(name='P', portal_type='LOCAL', output_format='CSV',
                              stream_type='PGW',
                              directory='{operator}/out/{vendor}/{ne}')
        d = portal.resolve_directory(operator='africell', vendor='huawei',
                                     network_element='pgw')
        self.assertTrue(self._norm(d).endswith('africell/out/huawei/pgw'), d)

    def test_unknown_segments_fall_back(self):
        portal = OutputPortal(name='P', portal_type='LOCAL', output_format='CSV',
                              stream_type='MSC', directory='')
        d = portal.resolve_directory()  # nothing supplied
        # operator/vendor -> 'unknown', ne -> stream_type lowercased
        self.assertTrue(self._norm(d).endswith('unknown/output/unknown/msc'), d)


class BuildFilenameTests(TestCase):
    databases = {'default'}

    def test_output_filename_keeps_original_name_swaps_extension(self):
        cdr = _FakeCDRFile('bFTMSX0100127.dat')
        portal = OutputPortal(name='BIGDATA', output_format='CSV', stream_type='MSC')
        self.assertEqual(_build_filename(cdr, portal, 'CSV'), 'bFTMSX0100127.csv')

    def test_raw_keeps_full_original_filename(self):
        cdr = _FakeCDRFile('bFTMSX0100127.dat')
        portal = OutputPortal(name='ARCHIVE', output_format='RAW', stream_type='MSC')
        self.assertEqual(_build_filename(cdr, portal, 'RAW'), 'bFTMSX0100127.dat')

    def test_no_vendor_or_operator_token_in_filename(self):
        cdr = _FakeCDRFile('myfile.dat')
        portal = OutputPortal(name='BIGDATA', output_format='JSON', stream_type='MSC')
        name = _build_filename(cdr, portal, 'JSON')
        self.assertEqual(name, 'myfile.json')
        for token in ('huawei', 'orange', 'BIGDATA'):
            self.assertNotIn(token, name)
