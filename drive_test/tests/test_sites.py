"""Network Reference / Sites page tests. Fixtures exist only in the isolated test DB."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import Cell, Chiefdom, District, Region, Sector, Site
from reference.models import Operator

STATIC = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})


def _site(op, code, name, lat=None, lon=None, chiefdom=None, active=True, cells=()):
    s = Site.objects.create(site_id=code, name=name, operator=op, latitude=lat, longitude=lon,
                            chiefdom=chiefdom, is_active=active)
    for i, (tech, n) in enumerate(cells):
        sec = Sector.objects.create(site=s, sector_id=f'{code}-S{i}')
        for j in range(n):
            Cell.objects.create(cell_id=f'{code}-{tech}-{i}{j}', operator=op, sector=sec, technology=tech)
    return s


@STATIC
class SitesTest(TestCase):
    databases = {'default'}

    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.viewer = U.objects.create_user('viewer', password='x')
        cls.admin = U.objects.create_user('radmin', password='x', is_regulatory_admin=True)
        cls.orange = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        cls.africell = Operator.objects.create(code='africell', name='Africell SL', home_plmn='61903', home_mcc='619', home_mnc='03')
        cls.west = Region.objects.create(code='W', name='Western Area')
        cls.north = Region.objects.create(code='N', name='Northern')
        cls.free = District.objects.create(code='FT', name='Freetown', region=cls.west)
        cls.bombali = District.objects.create(code='BO', name='Bombali', region=cls.north)
        cls.c1 = Chiefdom.objects.create(code='C1', name='Central I', district=cls.free)
        cls.c2 = Chiefdom.objects.create(code='C2', name='Makeni City', district=cls.bombali)
        cls.s1 = _site(cls.orange, 'FT-001', 'Freetown Central', 8.4841, -13.2299, cls.c1, cells=[('4G', 3), ('2G', 2)])
        cls.s2 = _site(cls.orange, 'MK-001', 'Makeni Hill', 8.88, -12.04, cls.c2, cells=[('3G', 1)])
        cls.s3 = _site(cls.africell, 'AF-001', 'No GPS Site', None, None, None, active=False)
        cls.s4 = _site(cls.africell, 'AF-002', 'Zero Zero', 0.0, 0.0, cls.c1)
        cls.url = reverse('drive_test:site_list')

    def setUp(self):
        self.client.force_login(self.viewer)

    def rows(self, **q):
        return [s.site_id for s in self.client.get(self.url, q).context['sites']]

    def test_summary_and_counts_are_real(self):
        r = self.client.get(self.url)
        self.assertEqual(r.context['summary'], {'total': 4, 'active': 3, 'operators': 2, 'gps': 2})
        self.assertEqual(r.context['missing_gps'], 2)   # missing and (0,0) both count as no GPS
        by = {s.site_id: s for s in r.context['sites']}
        self.assertEqual((by['FT-001'].n_sectors, by['FT-001'].n_cells), (2, 5))
        self.assertEqual(by['FT-001'].technologies, ['2G', '4G'])
        self.assertFalse(by['AF-002'].has_gps)
        self.assertNotContains(r, '0.0000, 0.0000')

    def test_no_n_plus_one(self):
        with self.assertNumQueries(11):   # bounded; independent of the number of sites
            self.client.get(self.url)

    def test_filters(self):
        self.assertEqual(sorted(self.rows(operator='africell')), ['AF-001', 'AF-002'])
        self.assertEqual(self.rows(region=self.north.pk), ['MK-001'])
        self.assertEqual(sorted(self.rows(district=self.free.pk)), ['AF-002', 'FT-001'])
        self.assertEqual(self.rows(technology='3G'), ['MK-001'])
        self.assertEqual(self.rows(technology='2G'), ['FT-001'])   # multi-tech site still found once
        self.assertEqual(self.rows(status='inactive'), ['AF-001'])
        self.assertEqual(sorted(self.rows(status='active')), ['AF-002', 'FT-001', 'MK-001'])
        self.assertEqual(self.rows(q='makeni'), ['MK-001'])
        self.assertEqual(self.rows(q='africell', status='inactive'), ['AF-001'])
        self.assertEqual(self.rows(region='abc'), self.rows())   # junk ids are ignored, not errors

    def test_pagination_and_page_sizes(self):
        r = self.client.get(self.url, {'per_page': 25})
        self.assertEqual(r.context['per_page'], 25)
        self.assertEqual(self.client.get(self.url, {'per_page': 7}).context['per_page'], 25)
        self.assertEqual(self.client.get(self.url, {'per_page': 100}).context['per_page'], 100)

    def test_empty_states(self):
        self.assertContains(self.client.get(self.url, {'q': 'zzz'}), 'No sites match your filters.')
        Site.objects.all().delete()
        r = self.client.get(self.url)
        self.assertContains(r, 'No network sites found')
        self.assertNotContains(r, 'Add Site')          # viewer cannot add

    def test_map_data_only_real_located_sites(self):
        d = self.client.get(reverse('drive_test:site_map_data')).json()
        self.assertEqual(sorted(f['properties']['site_id'] for f in d['features']), ['FT-001', 'MK-001'])
        f = next(f for f in d['features'] if f['properties']['site_id'] == 'FT-001')
        self.assertEqual(f['properties']['technology'], '2G / 4G')
        self.assertEqual(f['geometry']['coordinates'], [-13.2299, 8.4841])
        d = self.client.get(reverse('drive_test:site_map_data'), {'operator': 'africell'}).json()
        self.assertEqual(d['features'], [])

    def test_detail(self):
        r = self.client.get(reverse('drive_test:site_detail', args=[self.s1.pk]))
        self.assertEqual((r.context['cell_count'], len(r.context['sectors'])), (5, 2))
        self.assertEqual(r.context['technologies'], ['2G', '4G'])
        self.assertContains(r, 'Western Area')
        self.assertEqual(self.client.get(reverse('drive_test:site_detail', args=[999999])).status_code, 404)

    def test_write_access_requires_regulatory_admin(self):
        for name, args in (('site_create', []), ('site_edit', [self.s1.pk])):
            self.assertEqual(self.client.get(reverse(f'drive_test:{name}', args=args)).status_code, 302)
        self.assertEqual(self.client.post(reverse('drive_test:site_create'), {'site_id': 'X'}).status_code, 302)
        self.assertEqual(Site.objects.count(), 4)
        self.assertNotContains(self.client.get(self.url), '+ Add Site')

    def test_list_shows_actions_for_admin(self):
        self.client.force_login(self.admin)
        r = self.client.get(self.url)
        self.assertContains(r, '+ Add Site')
        self.assertContains(r, reverse('drive_test:reference_import'))
        self.assertContains(r, reverse('drive_test:site_edit', args=[self.s1.pk]))


@STATIC
class SiteFormTest(TestCase):
    databases = {'default'}

    def setUp(self):
        self.admin = get_user_model().objects.create_user('radmin', password='x', is_regulatory_admin=True)
        self.client.force_login(self.admin)
        self.op = Operator.objects.create(code='orange', name='Orange SL', home_plmn='61901', home_mcc='619', home_mnc='01')
        self.other = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        self.url = reverse('drive_test:site_create')

    def post(self, **over):
        data = {'site_id': 'S-1', 'name': 'Site One', 'operator': self.op.pk, 'site_type': 'macro',
                'latitude': '8.5', 'longitude': '-13.2', 'is_active': 'on'}
        data.update(over)
        return self.client.post(self.url, data)

    def test_create_valid(self):
        r = self.post()
        site = Site.objects.get(site_id='S-1')
        self.assertRedirects(r, reverse('drive_test:site_detail', args=[site.pk]))
        self.assertEqual((site.latitude, site.longitude, site.is_active), (8.5, -13.2, True))

    def test_validation_errors(self):
        cases = [({'latitude': '91'}, 'between -90 and 90'), ({'longitude': '181'}, 'between -180 and 180'),
                 ({'longitude': ''}, 'both latitude and longitude'), ({'latitude': '0', 'longitude': '0'}, 'not a valid site location'),
                 ({'name': ''}, 'required'), ({'operator': ''}, 'required'), ({'operator': 9999}, 'valid choice'),
                 ({'chiefdom': 9999}, 'valid choice')]
        for over, msg in cases:
            r = self.post(**over)
            self.assertEqual(r.status_code, 200, over)
            self.assertContains(r, msg)
        self.assertEqual(Site.objects.count(), 0)

    def test_duplicate_code_per_operator_only(self):
        self.post()
        self.assertContains(self.post(), 'already exists')
        self.assertEqual(self.post(operator=self.other.pk).status_code, 302)   # same code, other operator is fine

    def test_edit_updates_reference_but_not_operator_or_measurements(self):
        from datetime import datetime
        from drive_test.models import DriveTestFile, DriveTestSession, Measurement
        self.post()
        site = Site.objects.get(site_id='S-1')
        sec = Sector.objects.create(site=site, sector_id='A')
        cell = Cell.objects.create(cell_id='C', operator=self.op, sector=sec, technology='4G')
        sess = DriveTestSession.objects.create(operator=self.op, test_date='2026-01-01', uploaded_by=self.admin)
        f = DriveTestFile.objects.create(session=sess, original_filename='a', file_path='p', file_size=1, sha256='a' * 64)
        m = Measurement.objects.create(drive_file=f, sequence_num=1, captured_at=datetime(2026, 1, 1),
                                       latitude=8.5, longitude=-13.2, matched_cell=cell, match_method='exact_cgi')
        url = reverse('drive_test:site_edit', args=[site.pk])
        r = self.client.post(url, {'site_id': 'S-1', 'name': 'Renamed', 'operator': self.other.pk, 'site_type': 'macro',
                                   'latitude': '8.6', 'longitude': '-13.3'})
        self.assertEqual(r.status_code, 302)
        site.refresh_from_db(); m.refresh_from_db()
        self.assertEqual((site.name, site.latitude, site.is_active), ('Renamed', 8.6, False))
        self.assertEqual(site.operator, self.op)                      # operator change ignored
        self.assertEqual((m.latitude, m.matched_cell_id, m.match_method), (8.5, cell.pk, 'exact_cgi'))
