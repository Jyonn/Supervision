from datetime import timedelta
from email.utils import parsedate_to_datetime
import secrets
from unittest.mock import patch
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import TestCase,Client,override_settings
from django.utils import timezone
from .models import User

@override_settings(EMAIL_VERIFICATION_REQUIRED=False,SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_DOMAIN=None,SESSION_COOKIE_PATH='/',SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_AGE=7776000,SESSION_EXPIRE_AT_BROWSER_CLOSE=False,SESSION_SAVE_EVERY_REQUEST=False,FRIENDOM_ENABLED=True,FRIENDOM_EMAIL_ENABLED=True,FRIENDOM_CALLBACK_URL='https://testserver/api/auth/friendom/callback/')
class PersistentSessionTests(TestCase):
    def setUp(self):
        cache.clear();self.client=Client(enforce_csrf_checks=True);self.password=secrets.token_urlsafe(24)
    def post(self,path,body):
        csrf=self.client.get('/api/session/',secure=True).json()['csrf']
        return self.client.post(path,body,content_type='application/json',secure=True,HTTP_X_CSRFTOKEN=csrf,HTTP_REFERER='https://testserver/')
    def register(self):
        return self.post('/api/register/',{'email':'synthetic-session@example.test','name':'虚构会话验证','password':self.password,'password_confirm':self.password})
    def assert_deadline(self,response,before):
        self.assertIn(response.status_code,[200,201,302])
        session=self.client.session;deadline=session.get_expiry_date()
        self.assertGreaterEqual(deadline,before+timedelta(days=90))
        self.assertLessEqual(deadline,timezone.now()+timedelta(days=90))
        self.assertIsInstance(session.get('_session_expiry'),str)
        self.assertFalse(session.get_expire_at_browser_close())
        cookie=response.cookies[settings.SESSION_COOKIE_NAME]
        self.assertTrue(cookie['secure']);self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['domain'],'');self.assertEqual(cookie['path'],'/');self.assertEqual(cookie['samesite'],'Lax')
        self.assertLessEqual(int(cookie['max-age']),7776000);self.assertGreater(int(cookie['max-age']),7775990)
        self.assertLess(abs((parsedate_to_datetime(cookie['expires'])-deadline).total_seconds()),2)
        return deadline
    def test_registration_and_password_login_have_persistent_fixed_deadlines(self):
        before=timezone.now();self.assert_deadline(self.register(),before)
        self.post('/api/logout/',{})
        before=timezone.now();self.assert_deadline(self.post('/api/login/',{'email':'synthetic-session@example.test','password':self.password}),before)
    @patch('core.friendom.http_transport')
    def test_frienden_login_uses_same_persistent_deadline(self,transport):
        self.client.get('/api/auth/friendom/start/',secure=True)
        pending=self.client.session['friendom_pending']
        transport.side_effect=[{'access_token':'synthetic-token','token_type':'Bearer','scope':'profile email'},{'sub':'sermo:1:99999','nickname':'虚构会话','avatar_url':''}]
        before=timezone.now();response=self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':secrets.token_urlsafe(32)},secure=True)
        self.assert_deadline(response,before)
    def test_browsing_and_later_session_writes_do_not_extend_deadline(self):
        deadline=self.assert_deadline(self.register(),timezone.now()-timedelta(seconds=2))
        key=self.client.session.session_key
        later=timezone.now()+timedelta(days=1)
        with patch('django.utils.timezone.now',return_value=later),patch('django.contrib.sessions.middleware.time.time',return_value=later.timestamp()):
            response=self.client.get('/api/session/',secure=True)
            self.assertIsNotNone(response.json()['user']);self.assertNotIn(settings.SESSION_COOKIE_NAME,response.cookies)
            self.assertEqual(Session.objects.get(session_key=key).expire_date,deadline)
            response=self.client.get('/api/auth/friendom/start/',secure=True)
            self.assertEqual(response.status_code,302)
            self.assertEqual(Session.objects.get(session_key=key).expire_date,deadline)
            self.assertLess(int(response.cookies[settings.SESSION_COOKIE_NAME]['max-age']),7776000-86000)
            self.assertLess(abs((parsedate_to_datetime(response.cookies[settings.SESSION_COOKIE_NAME]['expires'])-deadline).total_seconds()),2)
    def test_expired_session_is_not_revived(self):
        self.register();key=self.client.session.session_key;deadline=self.client.session.get_expiry_date()
        with patch('django.utils.timezone.now',return_value=deadline+timedelta(seconds=1)):
            self.assertIsNone(self.client.get('/api/session/',secure=True).json()['user'])
            self.assertEqual(self.client.get('/api/tasks/',secure=True).status_code,403)
            self.assertEqual(Session.objects.get(session_key=key).expire_date,deadline)
    def test_logout_revokes_current_session_and_replay_fails(self):
        self.register();key=self.client.session.session_key;cookie=self.client.cookies[settings.SESSION_COOKIE_NAME].value
        response=self.post('/api/logout/',{})
        self.assertEqual(response.status_code,200);self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertEqual(str(response.cookies[settings.SESSION_COOKIE_NAME]['max-age']),'0')
        replay=Client();replay.cookies[settings.SESSION_COOKIE_NAME]=cookie
        self.assertIsNone(replay.get('/api/session/',secure=True).json()['user'])

    def test_legacy_login_keeps_its_existing_deadline(self):
        self.register();store=self.client.session;key=store.session_key
        store.pop('_session_expiry');store.save()
        old_deadline=timezone.now()+timedelta(hours=8)
        Session.objects.filter(session_key=key).update(expire_date=old_deadline)
        response=self.client.get('/api/session/',secure=True)
        self.assertIsNotNone(response.json()['user'])
        self.assertEqual(self.client.session.get_expiry_date(),old_deadline)
        self.assertEqual(Session.objects.get(session_key=key).expire_date,old_deadline)
        self.assertLess(int(response.cookies[settings.SESSION_COOKIE_NAME]['max-age']),28801)
    def test_expired_legacy_session_is_not_extended_by_new_default(self):
        self.register();store=self.client.session;key=store.session_key
        store.pop('_session_expiry');store.save()
        old_deadline=timezone.now()-timedelta(seconds=1)
        Session.objects.filter(session_key=key).update(expire_date=old_deadline)
        self.assertIsNone(self.client.get('/api/session/',secure=True).json()['user'])
        self.assertEqual(Session.objects.get(session_key=key).expire_date,old_deadline)
