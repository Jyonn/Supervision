import uuid
from datetime import timedelta
from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from .models import User,CalendarConnection,CalendarCredential
from .calendar_metadata import authorized_groups

class CalendarMetadataTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user=User.objects.create(username='synthetic-metadata')
        self.connection=CalendarConnection.objects.create(user=self.user,status='active',provider_subject='9',capabilities=['calendar.identity.read','calendar.categories.read'])
        self.credential=CalendarCredential.objects.create(connection=self.connection,ciphertext='synthetic-not-token',grant_id=str(uuid.uuid4()),expires_at=timezone.now()+timedelta(days=30),group_ids=[3,4])
    def identity(self,groups=None):
        data={'subject':'9','grant_id':self.credential.grant_id}
        if groups is not None:data['authorized_groups']=groups
        return data
    def test_only_authorized_names_and_safe_color_are_returned(self):
        data=[{'id':3,'name':'科研日历','color':'#789670'},{'id':4,'name':'阅读计划','color':'url(invalid)'},{'id':99,'name':'无权类别组','color':'#fff'}]
        with patch('core.calendar_authorization.private_transport',return_value=(200,data)) as transport:
            groups,status=authorized_groups(self.connection,self.credential)
        self.assertEqual(status,'ready');self.assertEqual([g['name'] for g in groups],['科研日历','阅读计划']);self.assertEqual(groups[1]['color'],'');self.assertEqual(transport.call_count,1)
    def test_missing_metadata_never_invents_names_or_requests_ungranted_scope(self):
        self.connection.capabilities=['calendar.identity.read']
        with patch('core.calendar_authorization.private_transport') as transport:
            groups,status=authorized_groups(self.connection,self.credential)
        self.assertEqual(groups,[]);self.assertEqual(status,'scope_metadata_unavailable');self.assertEqual(transport.call_count,0)
    def test_wrong_identity_and_duplicate_metadata_rejected(self):
        for data in [{'not':'a list'},[{'id':3,'name':'甲'},{'id':3,'name':'乙'}]]:
            with patch('core.calendar_authorization.private_transport',return_value=(200,data)):
                self.assertEqual(authorized_groups(self.connection,self.credential,refresh=True),([],'unavailable'))

    def test_connection_expiry_and_target_selection_use_real_clock(self):
        from django.test import override_settings
        self.client.force_login(self.user)
        with override_settings(CHRONELLE_AUTH_ENABLED=True),patch('core.calendar_metadata.authorized_groups',return_value=([],'scope_metadata_unavailable')):
            self.assertEqual(self.client.get('/api/calendar/connection/').status_code,200)
            self.assertEqual(self.client.post('/api/calendar/connection/',{'action':'select_group','group_id':3},content_type='application/json').status_code,200)
            self.credential.expires_at=timezone.now()-timedelta(seconds=1);self.credential.save(update_fields=['expires_at'])
            self.assertEqual(self.client.get('/api/calendar/connection/').json()['status'],'expired')
            self.assertEqual(self.client.post('/api/calendar/connection/',{'action':'select_group','group_id':4},content_type='application/json').status_code,400)
