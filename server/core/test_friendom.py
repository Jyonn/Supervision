import secrets
from unittest import TestCase
from urllib.parse import parse_qs, urlsplit
from .friendom import FriendomAdapter, FriendomError

class FriendomAdapterTests(TestCase):
    def adapter(self, transport):
        return FriendomAdapter(authorize_page='https://identity.example.test/oauth/authorize', token_url='https://identity.example.test/users/oauth/token', userinfo_url='https://identity.example.test/users/oauth/userinfo', client_id='supervision-unregistered-test', redirect_uri='http://127.0.0.1:8001/api/auth/friendom/callback/', transport=transport)
    def test_pkce_and_profile_only(self):
        url, pending = self.adapter(lambda *args: None).begin()
        q = parse_qs(urlsplit(url).query)
        self.assertEqual(q['scope'], ['profile'])
        self.assertEqual(q['code_challenge_method'], ['S256'])
        self.assertEqual(len(q['code_challenge'][0]), 43)
        self.assertGreaterEqual(len(pending['verifier']), 43)
    def test_state_mismatch_never_calls_provider(self):
        def no_network(*args):
            raise AssertionError('Transport must not be invoked')
        a = self.adapter(no_network)
        _, pending = a.begin()
        with self.assertRaises(FriendomError):
            a.complete(code=secrets.token_urlsafe(32), state='wrong', pending=pending)
    def test_stable_subject_not_name_or_email(self):
        def mock_transport(method, url, body, headers):
            if method == 'POST':
                return {'access_token': secrets.token_urlsafe(32), 'token_type': 'Bearer', 'scope': 'profile'}
            return {'sub': 'sermo:7:42', 'nickname': '测试昵称', 'email': 'unverified@example.test'}
        a = self.adapter(mock_transport)
        _, pending = a.begin()
        result = a.complete(code=secrets.token_urlsafe(32), state=pending['state'], pending=pending)
        self.assertEqual(result['subject'], 'sermo:7:42')
        self.assertNotIn('email', result)
    def test_forged_subject_rejected(self):
        def transport(method, *args):
            return {'access_token': 'temporary-test', 'token_type': 'Bearer', 'scope': 'profile'} if method == 'POST' else {'sub': 'email@example.test'}
        a = self.adapter(transport)
        _, pending = a.begin()
        with self.assertRaises(FriendomError):
            a.complete(code=secrets.token_urlsafe(32), state=pending['state'], pending=pending)
