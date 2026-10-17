from django.test import TestCase, Client, override_settings
from django.core.exceptions import ImproperlyConfigured
from supervision.deployment_config import chronelle_config

ORIGIN = 'https://abandon.qijiong.work'

class ChronelleProductionConfigTests(TestCase):
    def environment(self):
        return {'SUPERVISION_CHRONELLE_AUTH_ENABLED':'1','SUPERVISION_CHRONELLE_ENABLED':'1',
            'SUPERVISION_CHRONELLE_API_ORIGIN':'https://api.chronelle.jyonn.space',
            'SUPERVISION_CHRONELLE_AUTHORIZE_ORIGIN':'https://chronelle.jyonn.space',
            'SUPERVISION_CHRONELLE_APP_URL':'https://api.abandon.qijiong.work',
            'SUPERVISION_CHRONELLE_CALLBACK_URL':'https://api.abandon.qijiong.work/api/calendar/authorization/callback/'}
    def test_default_off_and_exact_complete_configuration(self):
        self.assertFalse(chronelle_config({})['CHRONELLE_AUTH_ENABLED'])
        self.assertFalse(chronelle_config({})['CHRONELLE_ENABLED'])
        self.assertTrue(chronelle_config(self.environment())['CHRONELLE_ENABLED'])
    def test_missing_mismatched_or_redirected_origin_fails_closed(self):
        for key in ['SUPERVISION_CHRONELLE_API_ORIGIN','SUPERVISION_CHRONELLE_AUTHORIZE_ORIGIN','SUPERVISION_CHRONELLE_APP_URL','SUPERVISION_CHRONELLE_CALLBACK_URL']:
            for value in ['', 'https://evil.example',self.environment()[key]+'?next=untrusted']:
                data={**self.environment(),key:value}
                with self.assertRaises(ImproperlyConfigured):chronelle_config(data)
    def test_sync_requires_authorization_and_strict_boolean(self):
        with self.assertRaises(ImproperlyConfigured):chronelle_config({'SUPERVISION_CHRONELLE_ENABLED':'1'})
        with self.assertRaises(ImproperlyConfigured):chronelle_config({'SUPERVISION_CHRONELLE_AUTH_ENABLED':'true'})

@override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN], CSRF_TRUSTED_ORIGINS=[ORIGIN],
                   MIDDLEWARE=['core.middleware.ExactOriginCors',
                               'django.contrib.sessions.middleware.SessionMiddleware',
                               'django.middleware.csrf.CsrfViewMiddleware',
                               'django.contrib.auth.middleware.AuthenticationMiddleware'])
class DeploymentBoundaryTests(TestCase):
    def test_preflight_exact_origin_headers_and_methods(self):
        client = Client()
        response = client.options('/api/session/', HTTP_ORIGIN=ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST', HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type,x-csrftoken')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['Access-Control-Allow-Origin'], ORIGIN)
        self.assertEqual(response['Access-Control-Allow-Credentials'], 'true')
        for origin, method, header in [('https://evil.example', 'POST', 'content-type'),
                                       (ORIGIN, 'PUT', 'content-type'), (ORIGIN, 'POST', 'authorization')]:
            response = client.options('/api/session/', HTTP_ORIGIN=origin,
                HTTP_ACCESS_CONTROL_REQUEST_METHOD=method, HTTP_ACCESS_CONTROL_REQUEST_HEADERS=header)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('Access-Control-Allow-Origin', response)

    def test_allowed_origin_does_not_bypass_csrf(self):
        client = Client(enforce_csrf_checks=True)
        token = client.get('/api/session/', HTTP_ORIGIN=ORIGIN).json()['csrf']
        missing = client.post('/api/logout/', HTTP_ORIGIN=ORIGIN)
        self.assertEqual(missing.status_code, 403)
        invalid_origin = client.post('/api/logout/', HTTP_ORIGIN='https://evil.example', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(invalid_origin.status_code, 403)
        accepted = client.post('/api/logout/', HTTP_ORIGIN=ORIGIN, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted['Access-Control-Allow-Origin'], ORIGIN)

    @override_settings(SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
    def test_csrf_cookie_secure_httponly_host_only(self):
        cookie = Client().get('/api/session/').cookies['supervision_csrf']
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['domain'], '')

    def test_untrusted_origin_no_cors_and_vary(self):
        response = Client().get('/api/session/', HTTP_ORIGIN='null')
        self.assertNotIn('Access-Control-Allow-Origin', response)
        self.assertIn('Origin', response['Vary'])
