"""
Upload page endpoint tests, run against the real TEMS Pocket .trp already in storage.
Skipped when that file is not present (e.g. CI). Uses an isolated test DB and temp storage.
"""
import glob
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from drive_test.models import DriveTestFile, DriveTestSession
from reference.models import Operator

_MATCHES = glob.glob(str(Path(settings.BASE_DIR) / 'data' / 'drive-test' / 'raw' / '*' / 'trp' / '*.trp'))
REAL_TRP = Path(_MATCHES[0]) if _MATCHES else None
AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


@override_settings(UMP_STORAGE_ROOT=tempfile.mkdtemp())
class UploadEndpointTest(TestCase):
    databases = {'default'}

    def setUp(self):
        self.user = get_user_model().objects.create_user('uptest', password='x')
        self.client.force_login(self.user)
        self.orange = Operator.objects.create(
            code='orange', name='Orange Sierra Leone', home_plmn='61901',
            home_mcc='619', home_mnc='01')
        self.url = reverse('drive_test:session_upload')

    def _post(self, name, data, **extra):
        return self.client.post(self.url, {'drive_test_file': SimpleUploadedFile(name, data), **extra}, **AJAX)

    @override_settings(STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
    def test_page_renders_supported_formats_from_registry(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'Upload Drive Test')
        self.assertContains(r, '.trp')

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_unsupported_extension_rejected_without_session(self):
        r = self._post('x.exe', b'MZ....')
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()['code'], 'unsupported_format')
        # An unknown extension should say the format is unsupported, and list
        # what is supported, rather than implying the file is damaged.
        self.assertIn('.exe', r.json()['message'])
        self.assertEqual(DriveTestSession.objects.count(), 0)

    def test_corrupt_trp_rejected_at_detection(self):
        """A file with the right extension and magic bytes but wrong contents.

        Detection used to accept this on extension + magic alone and only fail
        later, during parsing, as 'validation_failed'. Parsers now confirm
        structure — the TRP parser opens the archive and looks for the members
        it reads — so a corrupt file is refused at the door instead of being
        admitted and failing downstream.

        The message must distinguish 'damaged file' from 'unsupported format',
        because sending someone to convert a file that is merely truncated
        wastes their time.
        """
        r = self._post('fake.trp', b'PK not really a zip')
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()['code'], 'unsupported_format')

        message = r.json()['message'].lower()
        self.assertIn('.trp', message)
        self.assertTrue(
            'corrupt' in message or 'truncated' in message,
            f'message should say the file looks damaged, got: {message!r}',
        )
        self.assertEqual(DriveTestSession.objects.count(), 0)

    def test_no_file(self):
        r = self.client.post(self.url, {}, **AJAX)
        self.assertEqual(r.json()['code'], 'no_file')

    def test_csrf_enforced(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.user)
        r = c.post(self.url, {'drive_test_file': SimpleUploadedFile('a.csv', b'x')}, **AJAX)
        self.assertEqual(r.status_code, 403)

    def test_real_trp_full_flow_and_duplicate(self):
        if REAL_TRP is None:
            self.skipTest('no real .trp in storage')
        data = REAL_TRP.read_bytes()
        # The file has no SimOperator value, but its SIM IMSI (619 01...) identifies the operator.
        # With the user's operator choice the source is 'user'; with none it is auto-detected below.
        r = self._post('real.trp', data, title='Real test', test_type='outdoor',
                       operator=str(self.orange.pk))
        body = r.json()
        self.assertTrue(body['ok'], body)
        self.assertEqual(body['operator'], 'Orange Sierra Leone')
        self.assertEqual(body['operator_source'], 'user')
        self.assertTrue(body['checks']['has_gps'])

        s = self.client.get(body['status_url']).json()
        self.assertIn(s['file_status'], ('PARSING', 'MATCHING', 'NORMALIZING', 'COMPLETED', 'RECEIVED'))
        self.assertEqual(s['file_status'], 'COMPLETED', s)
        self.assertGreater(s['measurements'], 0)
        self.assertNotIn('\\', s['error'])
        session = DriveTestSession.objects.get(session_ref=body['session_ref'])
        self.assertEqual(session.metadata['operator_source'], 'user')
        self.assertEqual(session.title, 'Real test')

        dup = self._post('renamed.trp', data)
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(dup.json()['existing_session'], body['session_ref'])
        self.assertEqual(DriveTestSession.objects.count(), 1)

    def test_error_paths_are_scrubbed(self):
        from drive_test.views import _safe_error
        msg = _safe_error('FileNotFoundError: not found: C:\\Users\\x\\data\\a.trp\nTraceback...')
        self.assertNotIn('Users', msg)
        self.assertNotIn('Traceback', msg)
        self.assertNotIn('/srv/ump/data/f.trp', _safe_error('err /srv/ump/data/f.trp'))

    @override_settings(DRIVE_TEST_MAX_UPLOAD_BYTES=1000)
    def test_size_limit_is_configurable_and_enforced(self):
        r = self._post('big.trp', b'x' * 2000)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()['code'], 'too_large')
        self.assertEqual(DriveTestSession.objects.count(), 0)

    def test_default_limit_is_above_old_100mb(self):
        self.assertGreater(settings.DRIVE_TEST_MAX_UPLOAD_BYTES, 100 * 1024 * 1024)

    def test_batch_files_share_a_session(self):
        if REAL_TRP is None:
            self.skipTest('no real .trp in storage')
        data = REAL_TRP.read_bytes()
        first = self._post('a.trp', data, operator=str(self.orange.pk)).json()
        self.assertTrue(first['ok'], first)
        # A byte-different copy of the same real file so the SHA-256 differs.
        second = self._post('b.trp', data + b'\0' * 8, operator=str(self.orange.pk),
                            session_ref=first['session_ref']).json()
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['session_ref'], first['session_ref'])
        self.assertEqual(DriveTestSession.objects.count(), 1)
        self.assertEqual(DriveTestFile.objects.filter(session__session_ref=first['session_ref']).count(), 2)
        self.assertIn('file_measurements', self.client.get(second['status_url']).json())

    def test_batch_rejects_unknown_session_other_users_and_operator_mismatch(self):
        if REAL_TRP is None:
            self.skipTest('no real .trp in storage')
        data = REAL_TRP.read_bytes()
        r = self._post('a.trp', data, operator=str(self.orange.pk), session_ref='DT-NOPE')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['code'], 'bad_session')

        other = get_user_model().objects.create_user('someone', password='x')
        qcell = Operator.objects.create(code='qcell', name='Qcell', home_plmn='61905', home_mcc='619', home_mnc='05')
        theirs = DriveTestSession.objects.create(operator=self.orange, test_date='2026-01-01', uploaded_by=other)
        r = self._post('a.trp', data, operator=str(self.orange.pk), session_ref=theirs.session_ref)
        self.assertEqual(r.json()['code'], 'bad_session')          # cannot attach to someone else's session

        mine = DriveTestSession.objects.create(operator=qcell, test_date='2026-01-01', uploaded_by=self.user)
        r = self._post('a.trp', data, operator=str(self.orange.pk), session_ref=mine.session_ref)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()['code'], 'operator_mismatch')
        self.assertEqual(DriveTestFile.objects.count(), 0)

    def test_operator_auto_detected_from_sim_imsi(self):
        if REAL_TRP is None:
            self.skipTest('no real .trp in storage')
        from drive_test.views import _detect_operator
        self.assertEqual(_detect_operator({}, REAL_TRP), self.orange)
        Operator.objects.filter(pk=self.orange.pk).update(home_plmn='61999')
        self.assertIsNone(_detect_operator({}, REAL_TRP))           # unknown PLMN -> ask the user
        self.assertIsNone(_detect_operator({}, Path(__file__)))     # non-TRP file -> no crash, no guess
        # the preview endpoint never returns the IMSI
        Operator.objects.filter(pk=self.orange.pk).update(home_plmn='61901')
        r = self.client.post(reverse('drive_test:session_upload_preview'),
                             {'file': SimpleUploadedFile('r.trp', REAL_TRP.read_bytes())}, **AJAX).json()
        self.assertEqual(r['operator_id'], self.orange.pk)
        self.assertEqual(r['operator_source'], 'detected')
        self.assertNotIn('619017', str(r))
