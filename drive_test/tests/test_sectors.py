"""Network Reference / Sectors page tests. Fixtures exist only in the isolated test DB."""
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import (
    Cell, Chiefdom, District, DriveTestFile, DriveTestSession, Finding, Measurement, Region, Sector, Site,
)
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


@STATIC
class SectorsTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.viewer = U.objects.create_user('viewer', password='x')
        cls.admin = U.objects.create_user('radmin', password='x', is_regulatory_admin=True)
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.africell = Operator.objects.create(code='africell', name='Africell SL', home_plmn='61903', home_mcc='619', home_mnc='03')
        cls.west = Region.objects.create(code='W', name='Western Area')
        cls.free = District.objects.create(code='FT', name='Freetown', region=cls.west)
        cls.chief = Chiefdom.objects.create(code='C1', name='Central I', district=cls.free)
        cls.s1 = Site.objects.create(site_id='FT-001', name='Freetown Central', operator=cls.orange,
                                     latitude=8.48, longitude=-13.23, chiefdom=cls.chief)
        cls.s2 = Site.objects.create(site_id='AF-001', name='Africell Hill', operator=cls.africell)
        cls.a = Sector.objects.create(site=cls.s1, sector_id='A', azimuth_deg=65.0)
        cls.b = Sector.objects.create(site=cls.s1, sector_id='B', azimuth_deg=400.0, is_active=False)   # invalid azimuth
        cls.c = Sector.objects.create(site=cls.s2, sector_id='A')
        for i, tech in enumerate(['4G', '4G', '2G']):
            Cell.objects.create(cell_id=f'C{i}', operator=cls.orange, sector=cls.a, technology=tech)
        # inconsistent reference: an Orange cell under an Africell site
        Cell.objects.create(cell_id='ODD', operator=cls.orange, sector=cls.c, technology='3G')
        cls.url = reverse('drive_test:sector_list')

    def setUp(self):
        self.client.force_login(self.viewer)

    def rows(self, **q):
        return sorted(f'{s.site.site_id}/{s.sector_id}' for s in self.client.get(self.url, q).context['sectors'])

    def test_summary_and_row_data(self):
        r = self.client.get(self.url)
        self.assertEqual(r.context['summary'], {'total': 3, 'active': 2, 'sites': 2, 'cells': 4})
        by = {f'{s.site.site_id}/{s.sector_id}': s for s in r.context['sectors']}
        a = by['FT-001/A']
        self.assertEqual((a.n_cells, a.technologies, a.azimuth_deg), (3, ['2G', '4G'], 65.0))   # tech derived from cells
        self.assertEqual(by['FT-001/B'].technologies, [])
        self.assertContains(r, '65&deg;')

    def test_warnings_are_verifiable(self):
        by = {f'{s.site.site_id}/{s.sector_id}': s for s in self.client.get(self.url).context['sectors']}
        self.assertEqual(by['FT-001/A'].warnings, [])
        self.assertEqual(by['FT-001/B'].warnings, ['Invalid azimuth', 'No cells'])
        self.assertEqual(by['AF-001/A'].warnings, ['1 cell(s) belong to a different operator than the site'])

    def test_filters(self):
        self.assertEqual(self.rows(operator='africell'), ['AF-001/A'])
        self.assertEqual(self.rows(site='hill'), ['AF-001/A'])
        self.assertEqual(self.rows(technology='2G'), ['FT-001/A'])
        self.assertEqual(self.rows(technology='3G'), ['AF-001/A'])
        self.assertEqual(self.rows(region=self.west.pk), ['FT-001/A', 'FT-001/B'])
        self.assertEqual(self.rows(district=self.free.pk), ['FT-001/A', 'FT-001/B'])
        self.assertEqual(self.rows(status='inactive'), ['FT-001/B'])
        self.assertEqual(self.rows(q='central'), ['FT-001/A', 'FT-001/B'])
        self.assertEqual(self.rows(q='FT-001', status='active'), ['FT-001/A'])
        self.assertEqual(self.rows(region='junk'), self.rows())

    def test_no_n_plus_one_and_pagination(self):
        with self.assertNumQueries(12):
            self.client.get(self.url)
        self.assertEqual(self.client.get(self.url, {'per_page': 7}).context['per_page'], 25)

    def test_empty_states(self):
        self.assertContains(self.client.get(self.url, {'q': 'zzz'}), 'No sectors match your filters.')
        Cell.objects.all().delete(); Sector.objects.all().delete()
        r = self.client.get(self.url)
        self.assertContains(r, 'No network sectors found')
        self.assertNotContains(r, 'Add Sector')     # viewer

    def test_map_data_is_site_location_only(self):
        d = self.client.get(reverse('drive_test:sector_map_data')).json()
        self.assertEqual([f['properties']['site_id'] for f in d['features']], ['FT-001'])   # AF-001 has no coordinates
        self.assertEqual(d['features'][0]['properties']['sectors'], 2)
        d = self.client.get(reverse('drive_test:sector_map_data'), {'status': 'active'}).json()
        self.assertEqual(d['features'][0]['properties']['sectors'], 1)

    def test_detail(self):
        sess = DriveTestSession.objects.create(operator=self.orange, test_date='2026-01-01', uploaded_by=self.viewer)
        f = DriveTestFile.objects.create(session=sess, original_filename='a', file_path='p', file_size=1, sha256='a' * 64)
        m = Measurement.objects.create(drive_file=f, sequence_num=1, captured_at=datetime(2026, 1, 1, 10),
                                       latitude=8.4, longitude=-13.2, matched_cell=Cell.objects.get(cell_id='C0'))
        Finding.objects.create(session=sess, measurement=m, finding_type='WEAK_SIGNAL', severity='LOW',
                               cell=Cell.objects.get(cell_id='C0'), description='x')
        r = self.client.get(reverse('drive_test:sector_detail', args=[self.a.pk]))
        self.assertEqual((len(r.context['cells']), r.context['matched_count'], r.context['finding_count']), (3, 1, 1))
        self.assertEqual(r.context['technologies'], ['2G', '4G'])
        self.assertContains(r, 'Sectors have no coordinates of their own')
        self.assertContains(r, 'sector_pk=%d' % self.a.pk)
        r = self.client.get(reverse('drive_test:sector_detail', args=[self.b.pk]))
        self.assertContains(r, 'Invalid azimuth')
        self.assertContains(r, 'No cells are defined')
        self.assertEqual(self.client.get(reverse('drive_test:sector_detail', args=[999999])).status_code, 404)

    def test_cells_page_filters_by_exact_sector(self):
        r = self.client.get(reverse('drive_test:cell_list'), {'sector_pk': self.a.pk})
        self.assertEqual(sorted(c.cell_id for c in r.context['cells']), ['C0', 'C1', 'C2'])
        r = self.client.get(reverse('drive_test:cell_list'), {'sector_pk': self.c.pk})
        self.assertEqual([c.cell_id for c in r.context['cells']], ['ODD'])

    def test_write_access_and_sidebar(self):
        for name, args in (('sector_create', []), ('sector_edit', [self.a.pk])):
            self.assertEqual(self.client.get(reverse(f'drive_test:{name}', args=args)).status_code, 302)
        self.assertEqual(Sector.objects.count(), 3)
        self.assertContains(self.client.get(self.url), reverse('drive_test:sector_list'))   # sidebar entry
        self.assertNotContains(self.client.get(self.url), '+ Add Sector')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), '+ Add Sector')


@STATIC
class SectorFormTest(TestCase):
    databases = {'default'}

    def setUp(self):
        self.admin = get_user_model().objects.create_user('radmin', password='x', is_regulatory_admin=True)
        self.client.force_login(self.admin)
        self.op = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        self.other = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        self.site = Site.objects.create(site_id='S1', name='Site One', operator=self.op)
        self.qsite = Site.objects.create(site_id='Q1', name='Qcell Site', operator=self.other)
        self.url = reverse('drive_test:sector_create')

    def post(self, **over):
        data = {'operator': self.op.pk, 'site_code': 'S1', 'sector_id': 'A', 'azimuth_deg': '120', 'is_active': 'on'}
        data.update(over)
        return self.client.post(self.url, data)

    def test_create(self):
        r = self.post(height_m='30', tilt_deg='4')
        s = Sector.objects.get(sector_id='A')
        self.assertRedirects(r, reverse('drive_test:sector_detail', args=[s.pk]))
        self.assertEqual((s.site, s.azimuth_deg, s.height_m, s.tilt_deg, s.is_active), (self.site, 120.0, 30.0, 4.0, True))

    def test_validation(self):
        cases = [({'site_code': 'NOPE'}, 'Add the site first'), ({'site_code': 'Q1'}, 'Add the site first'),  # another operator's site
                 ({'azimuth_deg': '360'}, 'Azimuth must be'), ({'azimuth_deg': '-1'}, 'Azimuth must be'),
                 ({'height_m': '900'}, 'between 0 and 500'), ({'tilt_deg': '45'}, 'between -30 and 30'),
                 ({'sector_id': ''}, 'required'), ({'operator': ''}, 'required'), ({'operator': 9999}, 'valid choice')]
        for over, msg in cases:
            r = self.post(**over)
            self.assertEqual(r.status_code, 200, over)
            self.assertContains(r, msg)
        self.assertEqual(Sector.objects.count(), 0)

    def test_azimuth_optional(self):
        self.assertEqual(self.post(azimuth_deg='').status_code, 302)
        self.assertIsNone(Sector.objects.get(sector_id='A').azimuth_deg)

    def test_unique_per_site_only(self):
        self.post()
        self.assertContains(self.post(), 'already exists on site S1')
        self.assertEqual(self.post(operator=self.other.pk, site_code='Q1').status_code, 302)   # same code, other site

    def test_edit_keeps_operator_cells_and_measurements(self):
        self.post()
        sec = Sector.objects.get(sector_id='A')
        cell = Cell.objects.create(cell_id='C', operator=self.op, sector=sec, technology='4G')
        sess = DriveTestSession.objects.create(operator=self.op, test_date='2026-01-01', uploaded_by=self.admin)
        f = DriveTestFile.objects.create(session=sess, original_filename='a', file_path='p', file_size=1, sha256='a' * 64)
        m = Measurement.objects.create(drive_file=f, sequence_num=1, captured_at=datetime(2026, 1, 1), latitude=8.4,
                                       longitude=-13.2, matched_cell=cell, match_method='exact_ecgi')
        url = reverse('drive_test:sector_edit', args=[sec.pk])
        r = self.client.post(url, {'operator': self.other.pk, 'site_code': 'S1', 'sector_id': 'A-RENAMED', 'azimuth_deg': '240'})
        self.assertEqual(r.status_code, 302)
        sec.refresh_from_db(); cell.refresh_from_db(); m.refresh_from_db()
        self.assertEqual((sec.sector_id, sec.azimuth_deg, sec.is_active, sec.site), ('A-RENAMED', 240.0, False, self.site))
        self.assertEqual(cell.sector_id, sec.pk)                                    # cells stay attached
        self.assertEqual((m.matched_cell_id, m.match_method), (cell.pk, 'exact_ecgi'))   # history untouched
        self.assertContains(self.client.post(url, {'operator': self.op.pk, 'site_code': 'Q1', 'sector_id': 'A'}), 'Add the site first')
