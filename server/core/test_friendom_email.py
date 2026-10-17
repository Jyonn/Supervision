import hashlib
import secrets
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase, Client, override_settings
from .friendom import FriendomAdapter, FriendomError
from .models import User, FriendomIdentity

class EmailAdapterTests(TestCase):
    def adapter(self, email='avatar@example.invalid', verified=True, returned_scope='profile email'):
        def transport(method, *args):
            if method == 'POST':
                return {'access_token': 'fake-test-only', 'token_type': 'Bearer', 'scope': returned_scope}
            return {'sub': 'sermo:1:9', 'nickname': '示例', 'email': email, 'email_verified': verified}
        return FriendomAdapter(authorize_page='https://identity.example.invalid/authorize',
            token_url='https://identity.example.invalid/token', userinfo_url='https://identity.example.invalid/userinfo',
            client_id='local-test', redirect_uri='http://127.0.0.1:8001/api/auth/friendom/callback/', transport=transport)
    def test_explicit_incremental_scope_and_verified_hash_only(self):
        adapter = self.adapter(email=' Avatar@Example.Invalid ')
        url, pending = adapter.begin(scopes=('profile', 'email'))
        self.assertEqual(parse_qs(urlsplit(url).query)['scope'], ['profile email'])
        result = adapter.complete(code=secrets.token_urlsafe(32), state=pending['state'], pending=pending)
        self.assertEqual(result['gravatar_email_hash'], hashlib.sha256(b'avatar@example.invalid').hexdigest())
        self.assertNotIn('email', result)
    def test_unverified_missing_or_invalid_email_unusable(self):
        for email, verified in [('avatar@example.invalid', False), ('bad', True), (None, True), ('avatar@example.invalid', 'true')]:
            adapter = self.adapter(email=email, verified=verified)
            _, pending = adapter.begin(scopes=('profile', 'email'))
            result = adapter.complete(code=secrets.token_urlsafe(32), state=pending['state'], pending=pending)
            self.assertEqual(result['gravatar_email_hash'], '')
    def test_scope_downgrade_or_expansion_rejected(self):
        for returned in ['profile', 'profile email phone']:
            adapter = self.adapter(returned_scope=returned)
            _, pending = adapter.begin(scopes=('profile', 'email'))
            with self.assertRaises(FriendomError):
                adapter.complete(code=secrets.token_urlsafe(32), state=pending['state'], pending=pending)

@override_settings(FRIENDOM_ENABLED=True, FRIENDOM_EMAIL_ENABLED=True)
class EmailFlowTests(TestCase):
    host = {'HTTP_HOST': '127.0.0.1:8001'}
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user(username='identity-owner', email=None, first_name='示例用户')
        self.identity = FriendomIdentity.objects.create(user=self.user, subject='sermo:1:9')
        self.client.force_login(self.user)
    def start(self, consent=True):
        with patch('core.mail_verification.ready', return_value=False):
            csrf = self.client.get('/api/session/').json()['csrf']
        return self.client.post('/api/auth/friendom/email/start/', {'external_consent': consent},
            content_type='application/json', HTTP_X_CSRFTOKEN=csrf, **self.host)
    def complete(self, subject='sermo:1:9'):
        pending = self.client.session['friendom_pending']
        result = {'subject': subject, 'name': '示例', 'avatar_url': '', 'gravatar_email_hash': 'a'*64}
        with patch('core.friendom.approved_adapter') as factory:
            factory.return_value.complete.return_value = result
            return self.client.get('/api/auth/friendom/callback/', {'code': secrets.token_urlsafe(32), 'state': pending['state']}, **self.host)
    def test_requires_consent_csrf_and_feature_gate(self):
        self.assertEqual(self.start(False).status_code, 400)
        with override_settings(FRIENDOM_EMAIL_ENABLED=False):
            self.assertEqual(self.start().status_code, 503)
        self.assertEqual(self.client.post('/api/auth/friendom/email/start/', {}, content_type='application/json', **self.host).status_code, 403)
    def test_same_identity_hash_saved_without_login_email_or_network_selection(self):
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.complete().url, '/app/?friendom_email=ready')
        self.user.refresh_from_db(); self.identity.refresh_from_db()
        self.assertFalse(self.user.email)
        self.assertIsNone(self.user.email_verified_at)
        self.assertEqual(self.user.avatar_source, 'preset')
        self.assertEqual(self.identity.gravatar_email_hash, 'a'*64)
        self.assertEqual(User.objects.count(), 1)
    def test_other_subject_cannot_replace_identity(self):
        self.start()
        self.assertIn('friendom_error', self.complete('sermo:1:10').url)
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.gravatar_email_hash, '')
        self.assertEqual(FriendomIdentity.objects.count(), 1)
    def test_hash_source_requires_explicit_selection_and_consent(self):
        self.identity.gravatar_email_hash = 'b'*64
        self.identity.save()
        with patch('core.mail_verification.ready', return_value=False):
            csrf = self.client.get('/api/session/').json()['csrf']
        data = {'source': 'gravatar', 'gravatar_email_source': 'friendom'}
        def apply():
            return self.client.patch('/api/profile/', data, content_type='application/json', HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(apply().status_code, 400)
        data['external_consent'] = True
        result = apply()
        self.assertEqual(result.status_code, 200)
        self.assertIn('/avatar/'+'b'*64, result.json()['avatar_url'])
        self.user.refresh_from_db()
        self.assertFalse(self.user.email)
        self.assertIsNone(self.user.email_verified_at)
