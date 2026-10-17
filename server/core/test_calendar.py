import uuid
from datetime import datetime, timezone
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from .models import User,ResearchGroup,Membership,CalendarConnection,CalendarSharing,ResearchMeeting,MeetingSyncOperation
from .calendar_adapter import CalendarAdapter,CalendarError,CalendarWriteResult,ChronelleDelegatedAdapter
from .calendar_service import group_availability,sync_meeting

START='2026-10-20T09:00:00+00:00'
END='2026-10-20T12:00:00+00:00'

class MockCalendar(CalendarAdapter):
    def __init__(self):self.events={};self.calls=[];self.timeout_once=False;self.conflict=False
    def availability(self,connection,start,end,*,details=False):
        return [{'start':'2026-10-20T09:30:00Z','end':'2026-10-20T10:00:00Z','title':'不应意外泄露的私事'}]
    def write(self,connection,*,action,payload,idempotency_key,external_id,expected_version):
        self.calls.append((action,idempotency_key,external_id,expected_version,payload))
        if self.conflict:raise CalendarError('version_conflict')
        result=self.events.setdefault(idempotency_key,CalendarWriteResult(external_id or 'mock-event-1',str(len(self.events)+1)))
        if self.timeout_once:self.timeout_once=False;raise CalendarError('timeout')
        return result

@override_settings(CHRONELLE_ENABLED=False)
class CalendarTests(TestCase):
    def setUp(self):
        self.one=User.objects.create_user(username='one',email='one@example.invalid',first_name='一')
        self.two=User.objects.create_user(username='two',email='two@example.invalid',first_name='二')
        self.other=User.objects.create_user(username='outsider',email='other@example.invalid')
        self.group=ResearchGroup.objects.create(name='隔离测试组',owner=self.one)
        Membership.objects.create(group=self.group,user=self.one,role='mentor')
        Membership.objects.create(group=self.group,user=self.two,role='student')
        self.client=Client(enforce_csrf_checks=True);self.client.force_login(self.one)
        self.adapter=MockCalendar()
    def request(self,path,method='post',body=None):
        with patch('core.mail_verification.ready',return_value=False):csrf=self.client.get('/api/session/').json()['csrf']
        return getattr(self.client,method)(path,body or {},content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
    def connect(self,user,capabilities=None,share='busy'):
        connection=CalendarConnection.objects.create(user=user,status='active',provider_subject=f'mock:{user.id}',calendar_id=f'cal:{user.id}',event_group_id=1,capabilities=capabilities or ['read_busy','read_details','calendar.events.create','calendar.events.update','calendar.events.delete'])
        CalendarSharing.objects.create(user=user,group=self.group,visibility=share)
        return connection
    def body(self,**extra):
        return {'title':'本次周会','agenda':'## 议程\n讨论实验','start':START,'end':'2026-10-20T10:00:00Z','timezone':'UTC','kind':'weekly','participant_ids':[self.one.pk,self.two.pk],'request_key':str(uuid.uuid4()),**extra}
    def create(self,**extra):
        response=self.request(f'/api/groups/{self.group.id}/meetings/',body=self.body(**extra))
        self.assertEqual(response.status_code,201,response.content)
        return ResearchMeeting.objects.get(pk=response.json()['id'])
    def test_unknown_never_becomes_free_or_suggested(self):
        self.connect(self.one)
        result=group_availability(self.group,START,END,self.adapter)
        self.assertFalse(result['complete']);self.assertEqual(result['unknown_member_ids'],[self.two.pk]);self.assertEqual(result['suggestions'],[])
        self.assertNotIn('title',result['members'][0]['busy'][0])
        self.assertNotIn('不应',str(result))
    def test_details_opt_in_separate_from_write_permission_and_merged_intervals(self):
        self.connect(self.one,share='details');self.connect(self.two)
        result=group_availability(self.group,START,END,self.adapter)
        self.assertTrue(result['complete']);self.assertEqual(len(result['busy_union']),1)
        self.assertIn('title',result['members'][0]['busy'][0]);self.assertNotIn('title',result['members'][1]['busy'][0])
        self.assertEqual(len(result['suggestions']),2)
        CalendarSharing.objects.filter(user=self.one).update(visibility='none')
        self.assertFalse(group_availability(self.group,START,END,self.adapter)['complete'])
    def test_provider_failure_unknown_and_no_detail_leak(self):
        self.connect(self.one)
        with patch.object(self.adapter,'availability',side_effect=CalendarError('unavailable')):
            result=group_availability(self.group,START,END,self.adapter)
        self.assertEqual(result['members'][0]['state'],'unknown');self.assertEqual(result['busy_union'],[])
    def test_own_share_only_and_local_revoke_not_remote_cancel(self):
        self.connect(self.one)
        path=f'/api/groups/{self.group.id}/calendar/sharing/'
        self.assertEqual(self.request(path,'patch',{'visibility':'details','consent':True,'user_id':self.two.id}).status_code,400)
        self.assertEqual(self.request(path,'patch',{'visibility':'none','consent':True}).status_code,200)
        self.assertEqual(self.request('/api/calendar/connection/',body={'action':'revoke'}).status_code,200)
        self.assertEqual(CalendarConnection.objects.get(user=self.one).status,'revoked')
        self.assertEqual(CalendarSharing.objects.get(user=self.one).visibility,'none')
    def test_group_isolation_and_csrf(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/api/groups/{self.group.id}/meetings/').status_code,404)
        self.assertEqual(self.client.get(f'/api/groups/{self.group.id}/calendar/availability/?start={START}&end={END}').status_code,404)
        self.assertEqual(self.client.post('/api/calendar/connection/',{},content_type='application/json').status_code,403)
    def test_local_create_retry_same_key_no_duplicate_and_no_real_sync(self):
        body=self.body();path=f'/api/groups/{self.group.id}/meetings/'
        self.assertEqual(self.request(path,body=body).status_code,201)
        self.assertEqual(self.request(path,body=body).status_code,200)
        self.assertEqual(ResearchMeeting.objects.count(),1);self.assertEqual(MeetingSyncOperation.objects.count(),1)
        meeting=ResearchMeeting.objects.get()
        with patch('core.calendar_service.calendar_adapter') as factory:
            response=self.request(f'/api/meetings/{meeting.id}/',body={'action':'sync','revision':1})
            self.assertEqual(response.status_code,503);factory.assert_not_called()
    def test_organizer_revision_participant_and_time_validation(self):
        meeting=self.create()
        path=f'/api/meetings/{meeting.id}/'
        self.assertEqual(self.request(path,'patch',{'action':'cancel','revision':0}).status_code,409)
        self.client.force_login(self.two)
        self.assertEqual(self.request(path,'patch',{'action':'cancel','revision':1}).status_code,403)
        self.client.force_login(self.one)
        self.assertEqual(self.request(f'/api/groups/{self.group.id}/meetings/',body=self.body(participant_ids=[self.other.pk])).status_code,400)
        self.assertEqual(self.request(f'/api/groups/{self.group.id}/meetings/',body=self.body(start='2026-10-20T09:00:00')).status_code,400)
    def test_create_update_cancel_external_version_and_no_invitations(self):
        self.connect(self.one);meeting=self.create();path=f'/api/meetings/{meeting.id}/'
        sync_meeting(meeting.id,self.one,1,self.adapter)
        sync_meeting(meeting.id,self.one,1,self.adapter)
        self.assertEqual(len(self.adapter.calls),1)
        body=self.body(title='更新周会');body.pop('request_key');body['revision']=1
        self.assertEqual(self.request(path,'patch',body).status_code,200)
        sync_meeting(meeting.id,self.one,2,self.adapter)
        self.assertEqual(self.adapter.calls[-1][0],'update');self.assertEqual(self.adapter.calls[-1][2],'mock-event-1');self.assertEqual(self.adapter.calls[-1][3],'1')
        self.assertEqual(self.request(path,'patch',{'action':'cancel','revision':2}).status_code,200)
        sync_meeting(meeting.id,self.one,3,self.adapter)
        self.assertEqual(self.adapter.calls[-1][0],'cancel')
        self.assertTrue(all(call[4]['send_invitations'] is False for call in self.adapter.calls))
    def test_timeout_retry_retains_key_and_blocks_changes(self):
        self.connect(self.one);meeting=self.create();self.adapter.timeout_once=True
        with self.assertRaises(CalendarError):sync_meeting(meeting.id,self.one,1,self.adapter)
        self.assertEqual(self.request(f'/api/meetings/{meeting.id}/','patch',{'action':'cancel','revision':1}).status_code,409)
        sync_meeting(meeting.id,self.one,1,self.adapter)
        self.assertEqual(self.adapter.calls[0][1],self.adapter.calls[1][1]);self.assertEqual(len(self.adapter.events),1)
    def test_write_authority_does_not_follow_share_or_mentor_role(self):
        self.connect(self.one,capabilities=['read_busy'],share='details');meeting=self.create()
        with self.assertRaisesRegex(CalendarError,'write_not_authorized'):sync_meeting(meeting.id,self.one,1,self.adapter)
        self.assertEqual(self.adapter.calls,[])
    def test_version_conflict_revoke_and_connection_replacement(self):
        connection=self.connect(self.one);meeting=self.create();self.adapter.conflict=True
        with self.assertRaises(CalendarError):sync_meeting(meeting.id,self.one,1,self.adapter)
        self.assertEqual(meeting.sync_operations.get().status,'conflict')
        meeting.sync_operations.update(status='failed');connection.calendar_id='different-calendar';connection.save()
        with self.assertRaisesRegex(CalendarError,'connection_conflict'):sync_meeting(meeting.id,self.one,1,self.adapter)
    def test_local_cancel_before_sync_never_creates_remote_event(self):
        self.connect(self.one);meeting=self.create()
        self.request(f'/api/meetings/{meeting.id}/','patch',{'action':'cancel','revision':1})
        sync_meeting(meeting.id,self.one,2,self.adapter)
        self.assertEqual(self.adapter.calls,[])

class DelegatedContractTests(TestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.connection=SimpleNamespace(event_group_id=7)
        self.event_id=str(uuid.uuid4())
        self.payload={'title':'隔离会议','agenda':'Markdown','start':START,'end':END,'timezone':'UTC','send_invitations':False}
        self.calls=[]
        def transport(connection,method,path,body,headers):
            self.calls.append((method,path,body,headers))
            return (204,None) if method=='DELETE' else (201 if method=='POST' else 200,{'id':self.event_id,'revision':'2026-10-20T10:00:00.123456+00:00'})
        self.adapter=ChronelleDelegatedAdapter(transport)
    def write(self,action='create',**extra):
        return self.adapter.write(self.connection,action=action,payload=self.payload,idempotency_key='stable-test-key',external_id='' if action=='create' else self.event_id,expected_version='' if action=='create' else 'v1',**extra)
    def test_exact_routes_fields_idempotency_and_versions(self):
        created=self.write()
        self.assertEqual(created.external_id,self.event_id)
        self.assertEqual(self.calls[-1][0:2],('POST','/api/delegated/v1/events/'))
        self.assertEqual(self.calls[-1][2]['group_id'],7)
        self.assertNotIn('participant_ids',self.calls[-1][2])
        self.assertNotIn('send_invitations',self.calls[-1][2])
        self.write('update')
        self.assertEqual(self.calls[-1][0:2],('PATCH',f'/api/delegated/v1/event/{self.event_id}/'))
        self.assertEqual(self.calls[-1][2]['scope'],'series');self.assertEqual(self.calls[-1][2]['revision'],'v1')
        self.write('cancel')
        self.assertEqual(self.calls[-1][0],'DELETE')
        self.assertTrue(all(call[3]['Idempotency-Key']=='stable-test-key' for call in self.calls))
    def test_missing_revision_uncertain_never_guessed(self):
        self.adapter.transport=lambda *args:(201,{'id':self.event_id})
        with self.assertRaisesRegex(CalendarError,'uncertain'):self.write()
    def test_private_delegated_data_never_used_as_group_sharing(self):
        with self.assertRaisesRegex(CalendarError,'sharing_context_required'):
            self.adapter.availability(self.connection,START,END,details=True)
        self.assertEqual(self.calls,[])
    def test_invites_disabled_and_errors_not_leaked(self):
        self.payload['send_invitations']=True
        with self.assertRaisesRegex(CalendarError,'invitation_consent_required'):self.write()
        self.payload['send_invitations']=False
        self.adapter.transport=lambda *args:(403,{'detail':'private event title must not leak'})
        with self.assertRaisesRegex(CalendarError,'scope_denied'):self.write()
    def test_public_projection_credentialless_partial_and_title_filter(self):
        self.connection.calendar_id=str(uuid.uuid4())
        calls=[]
        def public(method,path,body,headers):
            calls.append((method,path,body,headers))
            return 200,{'calendar_ref':self.connection.calendar_id,'coverage':'partial','intervals':[
                {'start':START,'end':END,'all_day':False,'visibility':'busy','title':'private must be stripped'},
                {'start':START,'end':END,'all_day':False,'visibility':'published','title':'published title'}]}
        adapter=ChronelleDelegatedAdapter(lambda *args: self.fail('private transport called'),public_transport=public)
        result=adapter.availability(self.connection,datetime.fromisoformat(START),datetime.fromisoformat(END),details=True)
        self.assertFalse(result['complete']);self.assertNotIn('title',result['intervals'][0]);self.assertEqual(result['intervals'][1]['title'],'published title')
        self.assertEqual(calls[0][3],{});self.assertIsNone(calls[0][2]);self.assertIn('/api/public/v1/availability/',calls[0][1])
        result=adapter.availability(self.connection,datetime.fromisoformat(START),datetime.fromisoformat(END),details=False)
        self.assertTrue(all('title' not in item for item in result['intervals']))
    def test_partial_empty_is_not_free(self):
        user=User.objects.create(username='partial-case',email=None)
        group=ResearchGroup.objects.create(name='partial group',owner=user)
        Membership.objects.create(user=user,group=group,role='student')
        CalendarConnection.objects.create(user=user,status='active',calendar_id=str(uuid.uuid4()),capabilities=[])
        CalendarSharing.objects.create(user=user,group=group,visibility='busy')
        adapter=CalendarAdapter()
        with patch.object(adapter,'availability',return_value={'intervals':[],'complete':False}):
            result=group_availability(group,START,END,adapter)
        self.assertFalse(result['complete']);self.assertEqual(result['suggestions'],[]);self.assertEqual(result['members'][0]['state'],'partial')
    def test_expired_idempotency_response_never_reexecuted(self):
        self.adapter.transport=lambda *args:(410,{'detail':'expired'})
        with self.assertRaisesRegex(CalendarError,'idempotency_expired'):self.write()
