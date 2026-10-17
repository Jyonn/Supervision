import secrets,uuid,tempfile,base64,hashlib,logging
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,Client,override_settings
from django.utils import timezone
from .models import User,CalendarCredential,CalendarAuthorizationAttempt
from .calendar_vault import token_for
from .calendar_adapter import CalendarError
from .calendar_authorization import SCOPES
from .middleware import OAuthLogRedaction

@override_settings(CHRONELLE_AUTH_ENABLED=True,CHRONELLE_APP_URL='https://supervision.example.test',CHRONELLE_API_ORIGIN='https://chronelle.example.test',CHRONELLE_AUTHORIZE_ORIGIN='https://chronelle.example.test',CHRONELLE_CALLBACK_URL='',ALLOWED_HOSTS=['testserver','supervision.example.test','other.example.test'])
class CalendarAuthorizationTests(TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        override=override_settings(CALENDAR_VAULT_DIR=Path(self.directory.name)/'vault');override.enable();self.addCleanup(override.disable)
        self.user=User.objects.create(username='synthetic-calendar-owner',first_name='虚构',email=None)
        self.client=Client(enforce_csrf_checks=True);self.client.force_login(self.user)
        self.request_id=str(uuid.uuid4());self.grant_id=str(uuid.uuid4());self.reference=str(uuid.uuid4());self.token='cdg_'+secrets.token_urlsafe(32);self.state=None
        self.mock=patch('core.calendar_authorization.transport',side_effect=self.transport).start();self.addCleanup(patch.stopall)
    def transport(self,method,path,body=None,headers=None):
        if path=='/api/delegated-authorizations/requests/':
            self.state=body['state'];self.captured=body
            return 201,{'request_id':self.request_id,'authorize_url':'https://chronelle.example.test/#/authorize-delegated/'+self.request_id,'expires_in':600}
        if path.endswith('/claim/'):
            return 200,{'status':'approved','token_type':'Bearer','token':self.token,'state':self.state,'grant':{'id':self.grant_id,'scopes':list(SCOPES),'all_groups':False,'group_ids':[7],'expires_at':(timezone.now()+timedelta(days=1)).isoformat(),'paused':False,'revoked':False}}
        return 200,{'subject':'9','calendar_ref':self.reference,'grant_id':self.grant_id}
    def post(self,path,body=None):
        with patch('core.mail_verification.ready',return_value=False):csrf=self.client.get('/api/session/').json()['csrf']
        return self.client.post(path,body or {},content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
    def begin(self):
        result=self.post('/api/calendar/authorization/start/',{'consent':True});self.assertEqual(result.status_code,200,result.content);return result
    def test_pkce_and_public_response(self):
        result=self.begin();pending=self.client.session['calendar_pending']
        expected=base64.urlsafe_b64encode(hashlib.sha256(pending['verifier'].encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(self.captured['code_challenge'],expected);self.assertEqual(self.captured['code_challenge_method'],'S256')
        self.assertNotIn('token',result.json());self.assertNotIn('verifier',result.content.decode());self.assertFalse(CalendarCredential.objects.exists())
        self.assertEqual(self.captured['all_groups'],False)
        self.assertIsNone(self.captured['max_expiry_days'])
        self.assertFalse(any('invitations' in scope for scope in self.captured['scopes']))
        self.assertEqual(self.post('/api/calendar/authorization/start/',{'consent':True}).status_code,409)
    def test_claim_encryption_identity_and_single_use(self):
        self.begin();result=self.post('/api/calendar/authorization/claim/')
        self.assertEqual(result.json(),{'connected':True})
        credential=CalendarCredential.objects.get();self.assertNotIn(self.token,credential.ciphertext);self.assertEqual(credential.connection.provider_subject,'9');self.assertFalse(self.user.email)
        self.assertEqual((Path(self.directory.name)/'vault/key').stat().st_mode&0o777,0o600)
        calls=self.mock.call_count;self.assertEqual(self.post('/api/calendar/authorization/claim/').status_code,409);self.assertEqual(self.mock.call_count,calls)
    def test_explicit_null_expiry_is_permanent_not_missing_field(self):
        self.begin();original=self.transport
        def permanent(method,path,body=None,headers=None):
            status,data=original(method,path,body,headers)
            if path.endswith('/claim/'):data['grant']['expires_at']=None
            return status,data
        self.mock.side_effect=permanent
        self.assertEqual(self.post('/api/calendar/authorization/claim/').json(),{'connected':True})
        credential=CalendarCredential.objects.get()
        self.assertIsNone(credential.expires_at)
        self.assertEqual(token_for(credential.connection),self.token)
        public=self.client.get('/api/calendar/connection/').json()
        self.assertIsNone(public['grant_expires_at'])
        self.assertTrue(public['grant_permanent'])
        self.assertNotIn(self.token,str(public))
        self.assertLessEqual(self.client.session.get('calendar_pending',{}).get('expires',0),timezone.now().timestamp()+600)
    def test_missing_expiry_is_rejected_not_assumed_permanent(self):
        self.begin();original=self.transport
        def missing(method,path,body=None,headers=None):
            status,data=original(method,path,body,headers)
            if path.endswith('/claim/'):data['grant'].pop('expires_at')
            return status,data
        self.mock.side_effect=missing
        self.assertEqual(self.post('/api/calendar/authorization/claim/').status_code,409)
        self.assertFalse(CalendarCredential.objects.exists())
    def test_existing_expired_credential_is_never_extended(self):
        self.begin();self.post('/api/calendar/authorization/claim/')
        credential=CalendarCredential.objects.get()
        expired=timezone.now()-timedelta(days=1)
        credential.expires_at=expired;credential.save(update_fields=['expires_at'])
        with self.assertRaises(CalendarError):token_for(credential.connection)
        credential.refresh_from_db();self.assertEqual(credential.expires_at,expired)
    def test_cancel_and_late_claim_race(self):
        self.begin();self.post('/api/calendar/authorization/cancel/');calls=self.mock.call_count
        self.assertEqual(self.post('/api/calendar/authorization/claim/').status_code,409);self.assertEqual(self.mock.call_count,calls)
        self.request_id=str(uuid.uuid4());self.begin();original=self.transport
        def racing(method,path,body=None,headers=None):
            if path.endswith('/claim/'):CalendarAuthorizationAttempt.objects.filter(request_id=self.request_id).update(status='cancelled')
            return original(method,path,body,headers)
        self.mock.side_effect=racing;self.assertEqual(self.post('/api/calendar/authorization/claim/').status_code,409);self.assertFalse(CalendarCredential.objects.exists())
    @override_settings(CHRONELLE_CALLBACK_URL='https://supervision.example.test/api/calendar/authorization/callback/')
    def test_exact_https_callback_and_replay(self):
        self.begin();pending=self.client.session['calendar_pending'];calls=self.mock.call_count
        params={'request_id':self.request_id,'state':pending['state'],'status':'approved'}
        wrong=self.client.get('/api/calendar/authorization/callback/',params,secure=True,HTTP_HOST='other.example.test')
        self.assertIn('calendar_error',wrong.url);self.assertEqual(self.mock.call_count,calls)
        result=self.client.get('/api/calendar/authorization/callback/',params,secure=True,HTTP_HOST='supervision.example.test')
        self.assertEqual(result.url,'/app/?calendar_connected=1');self.assertTrue(CalendarCredential.objects.exists())
        self.assertIn('calendar_error',self.client.get('/api/calendar/authorization/callback/',params,secure=True,HTTP_HOST='supervision.example.test').url)
    def test_denial_expiry_and_legacy_key_rejected(self):
        self.begin();self.mock.side_effect=lambda *args:(403,None)
        self.assertEqual(self.post('/api/calendar/authorization/claim/').status_code,409)
        self.mock.side_effect=self.transport;self.request_id=str(uuid.uuid4());self.begin()
        pending=self.client.session['calendar_pending'];pending['expires']=0;s=self.client.session;s['calendar_pending']=pending;s.save()
        self.assertEqual(self.post('/api/calendar/authorization/claim/').status_code,409)
        self.request_id=str(uuid.uuid4());self.begin();original=self.transport
        def legacy(method,path,body=None,headers=None):
            status,data=original(method,path,body,headers)
            if path.endswith('/claim/'):data['token']='old-readonly-key'
            return status,data
        self.mock.side_effect=legacy;self.assertEqual(self.post('/api/calendar/authorization/claim/').status_code,409);self.assertFalse(CalendarCredential.objects.exists())
    def test_disconnect_and_remote_revocation(self):
        self.begin();self.post('/api/calendar/authorization/claim/');self.mock.side_effect=lambda *args:(401,None)
        result=self.post('/api/calendar/connection/',{'action':'check'});self.assertEqual(result.json()['status'],'unavailable');self.assertFalse(CalendarCredential.objects.exists())
        result=self.post('/api/calendar/connection/',{'action':'revoke'});self.assertFalse(result.json()['remote_revocation_supported'])
    def test_csrf_feature_gate_and_log_redaction(self):
        self.assertEqual(self.client.post('/api/calendar/authorization/start/',{},content_type='application/json').status_code,403)
        with override_settings(CHRONELLE_AUTH_ENABLED=False):self.assertEqual(self.post('/api/calendar/authorization/start/',{'consent':True}).status_code,503)
        record=logging.LogRecord('django.server',20,'',1,'"%s"',('GET /api/calendar/authorization/callback/?state=private&request_id=private HTTP/1.1',),None)
        OAuthLogRedaction().filter(record);self.assertNotIn('state=',record.getMessage());self.assertNotIn('request_id=',record.getMessage())
    @override_settings(FRONTEND_ORIGIN='https://supervision.qijiong.work',CHRONELLE_CALLBACK_URL='https://supervision.example.test/api/calendar/authorization/callback/')
    def test_production_callback_returns_to_fixed_frontend_without_secrets(self):
        self.begin();pending=self.client.session['calendar_pending']
        params={'request_id':self.request_id,'state':pending['state'],'status':'approved'}
        result=self.client.get('/api/calendar/authorization/callback/',params,secure=True,HTTP_HOST='supervision.example.test')
        self.assertEqual(result.url,'https://supervision.qijiong.work/?calendar_connected=1')
        self.assertNotIn(self.token,result.url)
        self.assertNotIn(pending['state'],result.url)
        self.assertNotIn(self.request_id,result.url)
    @override_settings(FRONTEND_ORIGIN='https://supervision.qijiong.work',CHRONELLE_CALLBACK_URL='https://supervision.example.test/api/calendar/authorization/callback/')
    def test_production_denial_and_logged_out_return_to_fixed_frontend(self):
        self.begin();pending=self.client.session['calendar_pending']
        params={'request_id':self.request_id,'state':pending['state'],'status':'denied'}
        result=self.client.get('/api/calendar/authorization/callback/',params,secure=True,HTTP_HOST='supervision.example.test')
        self.assertEqual(result.url,'https://supervision.qijiong.work/?calendar_error=claim')
        self.assertFalse(CalendarCredential.objects.exists())
        self.client.logout()
        result=self.client.get('/api/calendar/authorization/callback/',{'next':'https://evil.example'},secure=True,HTTP_HOST='supervision.example.test')
        self.assertEqual(result.url,'https://supervision.qijiong.work/?calendar_error=login')
