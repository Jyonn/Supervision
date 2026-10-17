from django.test import TestCase, Client, override_settings

ORIGIN = 'https://supervision.qijiong.work'

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
