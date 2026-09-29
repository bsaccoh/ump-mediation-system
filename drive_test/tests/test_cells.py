"""Network Reference / Cells page tests. Fixtures exist only in the isolated test DB."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, CellHistory, DriveTestFile, DriveTestSession, FrequencyBand, Measurement, Sector, Site,
)
from drive_test.services import cell_reference as ref
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class CellsTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.viewer = U.objects.create_user('viewer', password='x')
        cls.admin = U.objects.create_user('radmin', password='x', is_regulatory_admin=True)
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.africell = Operator.objects.create(code='africell', name='Africell SL', home_plmn='61903', home_mcc='619', home_mnc='03')
        cls.b3 = FrequencyBand.objects.create(band_number='3', technology='4G', frequency_mhz=1800)
        cls.b8 = FrequencyBand.objects.create(band_number='8', technology='2G', frequency_mhz=900)
        cls.site = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange, latitude=8.48, longitude=-13.23)
        cls.site2 = Site.objects.create(site_id='AF-001', name='Africell Hill', operator=cls.africell)
        cls.sec_a = Sector.objects.create(site=cls.site, sector_id='A')
        cls.sec_b = Sector.objects.create(site=cls.site, sector_id='B')
        cls.sec_c = Sector.objects.create(site=cls.site2, sector_id='A')

        def mk(cid, sec, tech, op=None, **kw):
            return Cell.objects.create(cell_id=cid, operator=op or cls.orange, sector=sec, technology=tech, **kw)

        # exact ECGI, own GPS, band
        cls.lte = mk('LTE-001', cls.sec_a, '4G', mcc='619', mnc='01', tac=100, eci=12345, pci=10, earfcn=1650,
                     band=cls.b3, latitude=8.4841, longitude=-13.2299)
        # exact CGI
        cls.gsm = mk('GSM-001', cls.sec_b, '2G', mcc='619', mnc='01', lac=20028, ci=10102, band=cls.b8, is_active=False)
        # limited: PCI+EARFCN only (no mcc/mnc -> no ecgi)
        cls.lim = mk('LTE-002', cls.sec_a, '4G', pci=77, earfcn=1650)
        # incomplete: nothing usable
        cls.inc = mk('NR-001', cls.sec_b, '5G')
        # other operator, invalid (0,0) coordinates only
        cls.other = mk('AF-CELL-1', cls.sec_c, '3G', op=cls.africell, latitude=0.0, longitude=0.0)
        cls.url = reverse('drive_test:cell_list')

    def setUp(self):
        self.client.force_login(self.viewer)

    def ids(self, **q):
        return sorted(c.cell_id for c in self.client.get(self.url, q).context['cells'])

    # -- state model ---------------------------------------------------------------------
    def test_states_python_and_database_agree(self):
        expected = {'LTE-001': ref.MATCHABLE, 'GSM-001': ref.MATCHABLE, 'LTE-002': ref.LIMITED,
                    'NR-001': ref.INCOMPLETE, 'AF-CELL-1': ref.INCOMPLETE}
        for cell in Cell.objects.all():
            self.assertEqual(ref.cell_state(cell), expected[cell.cell_id], cell.cell_id)
            in_db = [k for k, q in ref.STATE_Q.items() if Cell.objects.filter(pk=cell.pk).filter(q).exists()]
            self.assertEqual(in_db, [expected[cell.cell_id]], cell.cell_id)   # exactly one state each

    def test_warnings_are_real(self):
        self.assertEqual(ref.cell_warnings(self.lte), [])
        self.assertEqual(ref.cell_warnings(self.lim), ['Missing MCC/MNC', 'Missing TAC', 'Missing ECI'])
        self.assertIn('Invalid coordinates', ref.cell_warnings(self.other))
        self.assertIn('Duplicate identifier', ref.cell_warnings(self.lte, dup_ids=True))
        self.lte.band = self.b8
        self.assertIn('Band technology mismatch', ref.cell_warnings(self.lte))

    # -- list ------------------------------------------------------------------------------
    def test_summary(self):
        s = self.client.get(self.url).context['summary']
        self.assertEqual(s, {'total': 5, 'active': 4, 'matchable': 2, 'coords': 1})   # (0,0) not counted

    def test_filters(self):
        self.assertEqual(self.ids(operator='africell'), ['AF-CELL-1'])
        self.assertEqual(self.ids(technology='4G'), ['LTE-001', 'LTE-002'])
        self.assertEqual(self.ids(site='africell'), ['AF-CELL-1'])
        self.assertEqual(self.ids(site='FT-001', sector='b'), ['GSM-001', 'NR-001'])
        self.assertEqual(self.ids(band=self.b3.pk), ['LTE-001'])
        self.assertEqual(self.ids(status='inactive'), ['GSM-001'])
        self.assertEqual(self.ids(matching='matchable'), ['GSM-001', 'LTE-001'])
        self.assertEqual(self.ids(matching='limited'), ['LTE-002'])
        self.assertEqual(self.ids(matching='incomplete'), ['AF-CELL-1', 'NR-001'])
        self.assertEqual(self.ids(band='junk'), self.ids())

    def test_search_uses_real_identifier_fields(self):
        self.assertEqual(self.ids(q='lte-001'), ['LTE-001'])
        self.assertEqual(self.ids(q='619-01-12345'), ['LTE-001'])        # ECGI
        self.assertEqual(self.ids(q='619-01-20028-10102'), ['GSM-001'])  # CGI
        self.assertEqual(self.ids(q='12345'), ['LTE-001'])               # ECI
        self.assertEqual(self.ids(q='10102'), ['GSM-001'])               # CI
        self.assertEqual(self.ids(q='20028'), ['GSM-001'])               # LAC
        self.assertEqual(self.ids(q='100'), ['LTE-001'])                 # TAC
        self.assertEqual(self.ids(q='77'), ['LTE-002'])                  # PCI
        self.assertEqual(self.ids(q='hill'), ['AF-CELL-1'])              # site name

    def test_row_data(self):
        by = {c.cell_id: c for c in self.client.get(self.url).context['cells']}
        self.assertEqual(by['LTE-001'].identifier, ('ECI', 12345))
        self.assertEqual(by['LTE-001'].band_label, '4G Band 3 · 1800 MHz')
        self.assertEqual(by['LTE-001'].channel_label, 'EARFCN 1650')
        self.assertEqual(by['GSM-001'].identifier, ('CI', 10102))
        self.assertIsNone(by['NR-001'].identifier)
        self.assertTrue(by['LTE-001'].own_gps)
        self.assertFalse(by['LTE-002'].own_gps)
        self.assertTrue(by['LTE-002'].site_gps)         # shown as "Site location", never as cell coordinates
        r = self.client.get(self.url)
        self.assertContains(r, 'Site location')
        self.assertNotContains(r, '0.0000, 0.0000')

    def test_no_n_plus_one(self):
        with self.assertNumQueries(9):
            self.client.get(self.url)

    def test_duplicate_identifier_flagged(self):
        Cell.objects.filter(pk=self.lim.pk).update(ecgi=self.lte.ecgi)   # simulate a bad import
        by = {c.cell_id: c for c in self.client.get(self.url).context['cells']}
        self.assertIn('Duplicate identifier', by['LTE-001'].warnings)
        self.assertIn('Duplicate identifier', by['LTE-002'].warnings)
        self.assertNotIn('Duplicate identifier', by['GSM-001'].warnings)

    def test_pagination_and_empty_states(self):
        self.assertEqual(self.client.get(self.url, {'per_page': 7}).context['per_page'], 25)
        self.assertContains(self.client.get(self.url, {'q': 'zzz'}), 'No cells match your filters.')
        Cell.objects.all().delete()
        r = self.client.get(self.url)
        self.assertContains(r, 'No network cells found')
        self.assertNotContains(r, 'Add Cell')   # viewer

    def test_map_data(self):
        d = self.client.get(reverse('drive_test:cell_map_data')).json()
        cells = [f for f in d['features'] if f['properties']['ftype'] == 'cell']
        sites = [f for f in d['features'] if f['properties']['ftype'] == 'site']
        self.assertEqual([c['properties']['cell_id'] for c in cells], ['LTE-001'])   # only real own coordinates
        self.assertEqual(cells[0]['properties']['identifier'], 'ECI 12345')
        self.assertEqual([s['properties']['site_id'] for s in sites], ['FT-001'])
        self.assertEqual(self.client.get(reverse('drive_test:cell_map_data'), {'operator': 'africell'}).json()['features'], [])

    def test_detail(self):
        sess = DriveTestSession.objects.create(operator=self.orange, test_date='2026-01-01', uploaded_by=self.viewer)
        f = DriveTestFile.objects.create(session=sess, original_filename='a', file_path='p', file_size=1, sha256='a' * 64)
        Measurement.objects.create(drive_file=f, sequence_num=1, captured_at=datetime(2026, 1, 1, 10),
                                   latitude=8.4, longitude=-13.2, matched_cell=self.lte, match_method='exact_ecgi')
        r = self.client.get(reverse('drive_test:cell_detail', args=[self.lte.pk]))
        self.assertEqual(r.context['matched_count'], 1)
        self.assertEqual(dict(r.context['identity'])['ECGI'], '619-01-12345')
        self.assertNotIn('NCI', dict(r.context['identity']))        # absent identifiers are not shown
        self.assertContains(r, 'No reference issues found')
        r = self.client.get(reverse('drive_test:cell_detail', args=[self.lim.pk]))
        self.assertContains(r, 'Missing TAC')
        self.assertEqual(self.client.get(reverse('drive_test:cell_detail', args=[999999])).status_code, 404)

    def test_write_access_requires_regulatory_admin(self):
        for name, args in (('cell_create', []), ('cell_edit', [self.lte.pk])):
            self.assertEqual(self.client.get(reverse(f'drive_test:{name}', args=args)).status_code, 302)
        self.assertEqual(self.client.post(reverse('drive_test:cell_create'), {'cell_id': 'X'}).status_code, 302)
        self.assertEqual(Cell.objects.count(), 5)
        self.client.force_login(self.admin)
        r = self.client.get(self.url)
        self.assertContains(r, '+ Add Cell')
        self.assertContains(r, 'entity=cells')

    def test_import_link_preselects_entity(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('drive_test:reference_import'), {'entity': 'cells'})
        self.assertContains(r, 'value="cells" selected')


@STATIC
class CellFormTest(TestCase):
    databases = {'default'}

    def setUp(self):
        self.admin = get_user_model().objects.create_user('radmin', password='x', is_regulatory_admin=True)
        self.client.force_login(self.admin)
        self.op = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        self.other = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        self.site = Site.objects.create(site_id='S1', name='Site One', operator=self.op)
        Site.objects.create(site_id='Q1', name='Qcell Site', operator=self.other)
        self.b3 = FrequencyBand.objects.create(band_number='3', technology='4G', frequency_mhz=1800)
        self.url = reverse('drive_test:cell_create')

    def post(self, **over):
        data = {'cell_id': 'C-1', 'operator': self.op.pk, 'technology': '4G', 'site_code': 'S1', 'sector_code': 'A',
                'mcc': '619', 'mnc': '01', 'tac': '100', 'eci': '555', 'pci': '10', 'earfcn': '1650', 'is_active': 'on'}
        data.update(over)
        return self.client.post(self.url, data)

    def test_create_valid_computes_keys_history_and_sector(self):
        r = self.post(band=self.b3.pk)
        cell = Cell.objects.get(cell_id='C-1')
        self.assertRedirects(r, reverse('drive_test:cell_detail', args=[cell.pk]))
        self.assertEqual((cell.ecgi, cell.sector.site, cell.sector.sector_id, cell.band), ('619-01-555', self.site, 'A', self.b3))
        h = CellHistory.objects.get(cell=cell)
        self.assertEqual((h.change_type, h.changed_by, h.snapshot['eci']), ('created', self.admin, 555))
        self.assertEqual(Sector.objects.filter(site=self.site).count(), 1)
        self.post(cell_id='C-2', eci='556')                      # existing sector is reused
        self.assertEqual(Sector.objects.filter(site=self.site).count(), 1)

    def test_validation_errors(self):
        cases = [({'site_code': 'NOPE'}, 'Add the site first'), ({'site_code': 'Q1'}, 'Add the site first'),  # other operator's site
                 ({'technology': ''}, 'required'), ({'operator': ''}, 'required'), ({'cell_id': ''}, 'required'),
                 ({'mcc': '61'}, '3 digits'), ({'mnc': '1'}, '2 or 3 digits'), ({'pci': '504'}, 'between 0 and 503'),
                 ({'technology': '5G', 'pci': '1008'}, 'between 0 and 1007'), ({'tac': '99999999'}, 'TAC must be between'),
                 ({'latitude': '95', 'longitude': '1'}, 'between -90 and 90'), ({'latitude': '1'}, 'both latitude and longitude'),
                 ({'latitude': '0', 'longitude': '0'}, 'not a valid cell location'),
                 ({'technology': '2G', 'band': ''}, None), ({'band': 9999}, 'valid choice')]
        for over, msg in cases:
            r = self.post(**over)
            if msg is None:
                self.assertEqual(r.status_code, 302, over)
                Cell.objects.all().delete()
                continue
            self.assertEqual(r.status_code, 200, over)
            self.assertContains(r, msg)
        self.assertEqual(Cell.objects.count(), 0)

    def test_band_must_match_technology(self):
        self.assertContains(self.post(technology='2G', band=self.b3.pk), 'is a 4G band')

    def test_duplicate_code_per_operator_only(self):
        self.post()
        self.assertContains(self.post(), 'already exists')
        r = self.post(operator=self.other.pk, site_code='Q1')
        self.assertEqual(r.status_code, 302)

    def test_edit_recomputes_keys_keeps_operator_and_measurements(self):
        self.post()
        cell = Cell.objects.get(cell_id='C-1')
        sess = DriveTestSession.objects.create(operator=self.op, test_date='2026-01-01', uploaded_by=self.admin)
        f = DriveTestFile.objects.create(session=sess, original_filename='a', file_path='p', file_size=1, sha256='a' * 64)
        m = Measurement.objects.create(drive_file=f, sequence_num=1, captured_at=datetime(2026, 1, 1), latitude=8.4,
                                       longitude=-13.2, obs_eci=555, matched_cell=cell, match_method='exact_ecgi')
        url = reverse('drive_test:cell_edit', args=[cell.pk])
        # ECI removed and cell deactivated; operator tampering is ignored
        r = self.client.post(url, {'cell_id': 'C-1', 'operator': self.other.pk, 'technology': '4G', 'site_code': 'S1',
                                   'sector_code': 'A', 'mcc': '619', 'mnc': '01', 'tac': '100', 'pci': '10', 'earfcn': '1650'})
        self.assertEqual(r.status_code, 302)
        cell.refresh_from_db(); m.refresh_from_db()
        self.assertEqual((cell.operator, cell.eci, cell.ecgi, cell.is_active), (self.op, None, '', False))   # no stale ECGI
        self.assertEqual(list(CellHistory.objects.filter(cell=cell).values_list('change_type', flat=True).order_by('id')),
                         ['created', 'deactivated'])
        self.assertEqual((m.matched_cell_id, m.match_method, m.obs_eci), (cell.pk, 'exact_ecgi', 555))   # history untouched
