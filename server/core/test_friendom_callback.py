import logging
import secrets
import time
from unittest.mock import patch, Mock
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase, Client, override_settings
from .models import User, FriendomIdentity
from .middleware import OAuthLogRedaction

@override_settings(FRIENDOM_ENABLED=True)
class FriendomCallbackTests(TestCase):
    host = {'HTTP_HOST': '127.0.0.1:8001'}
    def begin(self, user=None):
        if user:
            self.client.force_login(user)
        response = self.client.get('/api/auth/friendom/start/', **self.host)
        self.assertEqual(response.status_code, 302)
        return self.client.session['friendom_pending']
    def callback(self, pending, **extra):
        return self.client.get('/api/auth/friendom/callback/', {'code': secrets.token_urlsafe(32), 'state': pending['state'], **extra}, **self.host)
    def test_start_uses_only_approved_client_and_callback(self):
        r=self.client.get('/api/auth/friendom/start/', **self.host)
        self.assertEqual(urlsplit(r.url).scheme,'https')
        self.assertEqual(urlsplit(r.url).netloc,'frienden.jyonn.space')
        self.assertEqual(urlsplit(r.url).path,'/oauth/authorize')
        self.assertEqual(parse_qs(urlsplit(r.url).query)['redirect_uri'],['http://127.0.0.1:8001/api/auth/friendom/callback/'])
        self.assertEqual(parse_qs(urlsplit(r.url).query)['code_challenge_method'],['S256'])
        self.assertIn('client_id=supervision-local', r.url)
        self.assertIn('scope=profile', r.url)
        self.assertNotIn('scope=phone', r.url)
        self.assertGreater(self.client.session['friendom_pending']['expires'], time.time())
        self.assertEqual(parse_qs(urlsplit(r.url).query)['scope'], ['profile email'])
    @override_settings(FRIENDOM_EMAIL_ENABLED=False)
    def test_profile_only_registration_and_pending_remain_compatible(self):
        r=self.client.get('/api/auth/friendom/start/', **self.host)
        self.assertEqual(parse_qs(urlsplit(r.url).query)['scope'], ['profile'])
    @patch('core.friendom.http_transport')
    def test_normal_login_email_hash_saved_without_email_merge_or_gravatar_selection(self, transport):
        User.objects.create_user(username='local-email',email='same@example.test')
        for index, (email, verified) in enumerate([('same@example.test',True),(None,True),('bad',True),('same@example.test',False)]):
            self.client.logout()
            pending=self.begin()
            transport.side_effect=[{'access_token':'synthetic-token','token_type':'Bearer','scope':'profile email'},
                {'sub':f'sermo:1:{99+index}','nickname':'Synthetic','email':email,'email_verified':verified}]
            self.assertEqual(self.callback(pending).url,'/app/')
            identity=FriendomIdentity.objects.get(subject=f'sermo:1:{99+index}')
            self.assertIsNone(identity.user.email)
            self.assertIsNone(identity.user.email_verified_at)
            self.assertNotEqual(identity.user.avatar_source,'gravatar')
            self.assertEqual(User.objects.count(),2+index)
            self.assertEqual(len(identity.gravatar_email_hash),64 if index==0 else 0)
    @patch('core.friendom.http_transport')
    def test_denial_does_not_retry_or_contact_provider(self, transport):
        pending=self.begin()
        response=self.client.get('/api/auth/friendom/callback/',{'error':'access_denied','state':pending['state']},**self.host)
        self.assertIn('friendom_error',response.url)
        self.assertNotIn('friendom_pending',self.client.session)
        transport.assert_not_called()
    @patch('core.friendom.approved_adapter')
    def test_valid_callback_creates_unusable_password_account_without_email_merge(self, factory):
        existing=User.objects.create_user(username='existing', email='local@example.test')
        factory.return_value.begin.return_value=('https://identity.example.test/', {'state':secrets.token_urlsafe(32),'verifier':secrets.token_urlsafe(48)})
        pending=self.begin()
        factory.return_value.complete.return_value={'subject':'sermo:1:99','name':'授权用户','avatar_url':''}
        response=self.callback(pending)
        self.assertEqual(response.url, '/app/')
        identity=FriendomIdentity.objects.get(subject='sermo:1:99')
        self.assertNotEqual(identity.user_id, existing.id)
        self.assertIsNone(identity.user.email)
        self.assertFalse(identity.user.has_usable_password())
        self.assertEqual(self.client.session['_auth_user_id'], str(identity.user_id))
        self.assertNotIn('friendom_pending', self.client.session)
        self.assertIn('friendom_error', self.callback(pending).url)
    @patch('core.friendom.http_transport')
    def test_state_mismatch_never_contacts_provider(self, transport):
        pending=self.begin()
        response=self.callback(pending, state='wrong')
        self.assertIn('friendom_error', response.url)
        transport.assert_not_called()
        self.assertFalse(FriendomIdentity.objects.exists())
    @patch('core.friendom.http_transport')
    def test_expired_pending_is_rejected_without_network(self, transport):
        pending=self.begin();pending['expires']=0
        s=self.client.session;s['friendom_pending']=pending;s.save()
        self.assertIn('friendom_error', self.callback(pending).url)
        transport.assert_not_called()
    @patch('core.friendom.approved_adapter')
    def test_link_cannot_take_another_accounts_identity(self, factory):
        one=User.objects.create_user(username='one',email='one@example.test')
        two=User.objects.create_user(username='two',email='two@example.test')
        FriendomIdentity.objects.create(user=one,subject='sermo:1:1')
        factory.return_value.begin.return_value=('https://identity.example.test/', {'state':secrets.token_urlsafe(32),'verifier':secrets.token_urlsafe(48)})
        pending=self.begin(two)
        factory.return_value.complete.return_value={'subject':'sermo:1:1','name':'one','avatar_url':''}
        self.assertIn('friendom_error',self.callback(pending).url)
        self.assertFalse(FriendomIdentity.objects.filter(user=two).exists())
    @patch('core.friendom.approved_adapter')
    def test_link_requires_same_session_user(self,factory):
        one=User.objects.create_user(username='one',email='one@example.test')
        factory.return_value.begin.return_value=('https://identity.example.test/', {'state':secrets.token_urlsafe(32),'verifier':secrets.token_urlsafe(48)})
        self.begin(one)
        factory.reset_mock()
        pending=self.client.session['friendom_pending']
        self.client.logout()
        self.assertIn('friendom_error',self.callback(pending).url)
        factory.assert_not_called()
    def test_callback_log_redacts_code_and_state(self):
        record=logging.LogRecord('django.server',20,'',1,'"%s" %s %s',('GET /api/auth/friendom/callback/?code=private&state=private HTTP/1.1',302,0),None)
        OAuthLogRedaction().filter(record)
        self.assertNotIn('code=',record.getMessage())
        self.assertNotIn('state=',record.getMessage())
