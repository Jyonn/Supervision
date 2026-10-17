"""Local polling is explicit and never reuses a production credential."""
import uuid
from unittest.mock import patch
from django.test import TestCase,override_settings
from rest_framework.test import APIRequestFactory,force_authenticate
from .models import User
from . import calendar_authorization,calendar_views
from supervision import local_connected

LOCAL={name:getattr(local_connected,name) for name in ['CHRONELLE_ENABLED','CHRONELLE_AUTH_ENABLED','CHRONELLE_API_ORIGIN','CHRONELLE_AUTHORIZE_ORIGIN','CHRONELLE_APP_URL','CHRONELLE_CALLBACK_URL','CHRONELLE_APP_NAME','CHRONELLE_PURPOSE']}

class LocalConnectionTests(TestCase):
    def setUp(self):self.user=User.objects.create(username='synthetic-local-connect',first_name='虚构本地验证')

    @override_settings(**LOCAL)
    def test_explicit_polling_identity_and_unchanged_scopes(self):
        request=APIRequestFactory().post('/api/calendar/authorization/start/',{})
        request.user=self.user;request.session={}
        request_id=str(uuid.uuid4())
        response={'request_id':request_id,'authorize_url':LOCAL['CHRONELLE_AUTHORIZE_ORIGIN']+'/#/authorize-delegated/'+request_id,'expires_in':600}
        with patch.object(calendar_authorization,'transport',return_value=(201,response)) as transport:
            calendar_authorization.begin(request)
        payload=transport.call_args.args[2]
        self.assertIn('本机8001',payload['app_name'])
        self.assertIn('凭据仅保存于本机',payload['purpose'])
        self.assertEqual(payload['redirect_uri'],'')
        self.assertFalse(payload['all_groups'])
        self.assertEqual(payload['scopes'],list(calendar_authorization.SCOPES))
        self.assertFalse(any('invitations' in scope for scope in payload['scopes']))

    @override_settings(**LOCAL)
    def test_real_local_connection_endpoint_is_ready(self):
        request=APIRequestFactory().get('/api/calendar/connection/')
        request.session={}
        force_authenticate(request,self.user)
        response=calendar_views.connection(request)
        self.assertTrue(response.data['auth_ready'])
        self.assertTrue(response.data['provider_ready'])
        self.assertEqual(response.data['status'],'disconnected')

    @override_settings(CHRONELLE_AUTH_ENABLED=False,LOCAL_REVIEW_FIXTURE=True)
    def test_fixture_does_not_advertise_real_authorization(self):
        request=APIRequestFactory().get('/api/calendar/connection/')
        request.session={}
        force_authenticate(request,self.user)
        response=calendar_views.connection(request)
        self.assertFalse(response.data['auth_ready'])
        self.assertTrue(response.data['is_fixture'])
        self.assertEqual(response.data['auth_unavailable_reason'],'review_fixture')
