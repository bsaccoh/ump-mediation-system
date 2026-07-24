"""DJ Phase 5 — operator-selector middleware + switch view."""
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core.middleware import OperatorContextMiddleware, resolve_active_operator, SESSION_KEY
from core.operator_context import get_operator


class ResolveActiveOperatorTests(TestCase):
    databases = {'default'}

    def setUp(self):
        self.rf = RequestFactory()

    def _req(self, session=None):
        r = self.rf.get('/')
        r.session = session or {}
        return r

    def test_default_when_no_session(self):
        self.assertEqual(resolve_active_operator(self._req()), settings.DEFAULT_OPERATOR)

    def test_value_from_session(self):
        self.assertEqual(
            resolve_active_operator(self._req({SESSION_KEY: 'africell'})), 'africell')

    def test_invalid_value_falls_back(self):
        self.assertEqual(
            resolve_active_operator(self._req({SESSION_KEY: 'nope'})),
            settings.DEFAULT_OPERATOR)


class OperatorMiddlewareTests(TestCase):
    databases = {'default'}

    def test_sets_context_during_request_and_clears_after(self):
        captured = {}

        def get_response(request):
            captured['op'] = get_operator()
            return HttpResponse('ok')

        mw = OperatorContextMiddleware(get_response)
        req = RequestFactory().get('/')
        req.session = {SESSION_KEY: 'africell'}
        resp = mw(req)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured['op'], 'africell')   # set during the request
        self.assertEqual(req.active_operator, 'africell')
        # Context cleared afterwards -> falls back to default.
        self.assertEqual(get_operator(), settings.DEFAULT_OPERATOR)


class SetOperatorViewTests(TestCase):
    databases = {'default'}

    def setUp(self):
        from django.contrib.auth import get_user_model
        U = get_user_model()
        self.user = U.objects.create_user(username='op', password='x')
        self.client.login(username='op', password='x')

    def test_switch_sets_session(self):
        self.client.post('/set-operator/', {'operator': 'africell'})
        self.assertEqual(self.client.session.get(SESSION_KEY), 'africell')

    def test_switch_rejects_unknown(self):
        self.client.post('/set-operator/', {'operator': 'bogus'})
        self.assertIsNone(self.client.session.get(SESSION_KEY))
