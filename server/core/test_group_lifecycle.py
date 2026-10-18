import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch, Mock
from django.test import TestCase, Client
from django.utils import timezone
from .models import (User, ResearchGroup, Membership, ResearchTask, Entry, GroupSpaceBinding,
    GroupInvitation, InvitationAcceptance, SpaceIdentity, CalendarConnection, CalendarCredential,
    CalendarSharing, ResearchMeeting, MeetingCalendarLink, MeetingSyncOperation, GroupActionReceipt)
from .group_join import digest
from .calendar_service import sync_meeting
from .calendar_adapter import CalendarError


class GroupLifecycleTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='owner',email='owner@example.test')
        self.student=User.objects.create_user(username='student',email='student@example.test')
        self.mentor=User.objects.create_user(username='mentor',email='mentor@example.test')
        self.outsider=User.objects.create_user(username='outsider',email='outsider@example.test')
        self.a=ResearchGroup.objects.create(name='实验甲组（虚构）',owner=self.owner)
        self.b=ResearchGroup.objects.create(name='其他研究组（虚构）',owner=self.student)
        for user,role in [(self.owner,'mentor'),(self.student,'student'),(self.mentor,'mentor')]:
            Membership.objects.create(group=self.a,user=user,role=role)
        Membership.objects.create(group=self.b,user=self.student,role='mentor')
        Membership.objects.create(group=self.b,user=self.owner,role='student')
        self.task=ResearchTask.objects.create(group=self.a,creator=self.owner,title='甲组任务',description='本组说明及图片外链')
        self.task.assignees.add(self.student,self.owner)
        for kind in ['report','summary','feedback']:
            Entry.objects.create(task=self.task,owner=self.student,kind=kind,day=timezone.now().date(),body='本组内容')
        self.other_task=ResearchTask.objects.create(group=self.b,creator=self.student,title='其他组任务')
        self.other_task.assignees.add(self.student)
        self.personal=ResearchTask.objects.create(creator=self.student,title='个人任务');self.personal.assignees.add(self.student)
        self.binding=GroupSpaceBinding.objects.create(group=self.a,space_id=123,admin_subject='sermo:123:1',verified_at=timezone.now(),expires_at=timezone.now()+timedelta(days=1))
        self.token='A'*43
        self.invite=GroupInvitation.objects.create(group=self.a,token_hash=digest(self.token),binding_revision=1,expires_at=timezone.now()+timedelta(hours=1),created_by=self.owner)
        InvitationAcceptance.objects.create(invitation=self.invite,user=self.student)
        SpaceIdentity.objects.create(user=self.student,space_id=123,subject='sermo:123:2')
        self.connection=CalendarConnection.objects.create(user=self.owner,status='active',provider_subject='synthetic-owner',calendar_id='synthetic-calendar')
        self.credential=CalendarCredential.objects.create(connection=self.connection,ciphertext='synthetic-not-a-real-credential',grant_id='synthetic')
        CalendarSharing.objects.create(user=self.student,group=self.a,visibility='busy')
        CalendarSharing.objects.create(user=self.student,group=self.b,visibility='details')
        self.meeting=ResearchMeeting.objects.create(group=self.a,organizer=self.owner,title='甲组会议',agenda='本组纪要',starts_at=timezone.now(),ends_at=timezone.now()+timedelta(hours=1))
        self.meeting.participants.add(self.owner,self.student)
        self.link=MeetingCalendarLink.objects.create(meeting=self.meeting,connection=self.connection,provider_subject='synthetic-owner',calendar_id='synthetic-calendar',external_id='synthetic-external-event')
        self.operation=MeetingSyncOperation.objects.create(meeting=self.meeting,revision=1,action='create',status='synced')
        self.other_invite=GroupInvitation.objects.create(group=self.b,token_hash=digest('B'*43),binding_revision=1,expires_at=timezone.now()+timedelta(hours=1),created_by=self.student)
        InvitationAcceptance.objects.create(invitation=self.other_invite,user=self.owner)
        self.other_entry=Entry.objects.create(task=self.other_task,owner=self.student,kind='report',day=timezone.now().date(),body='其他组保留内容')
        self.other_meeting=ResearchMeeting.objects.create(group=self.b,organizer=self.owner,title='其他组会议',starts_at=timezone.now(),ends_at=timezone.now()+timedelta(hours=1))
        self.other_link=MeetingCalendarLink.objects.create(meeting=self.other_meeting,connection=self.connection,provider_subject='synthetic-owner',calendar_id='synthetic-calendar',external_id='other-synthetic-event')
        self.client.force_login(self.owner)
    def body(self,action='delete',**extra):
        result={'action':action,'confirm_name':self.a.name,'request_key':str(uuid.uuid4())}
        if action=='delete':result['confirm_permanent']=True
        return {**result,**extra}
    def post(self,body,group=None):
        return self.client.post(f'/api/groups/{group or self.a.pk}/lifecycle/',body,content_type='application/json')
    def test_owner_flag_comes_from_owner_not_mentor_role(self):
        groups=self.client.get('/api/groups/').json()
        self.assertEqual({g['id']:g['is_owner'] for g in groups},{self.a.id:True,self.b.id:False})
        self.client.force_login(self.mentor)
        self.assertFalse(self.client.get('/api/groups/').json()[0]['is_owner'])
        self.assertEqual(self.post(self.body()).status_code,403)
        self.assertTrue(ResearchGroup.objects.filter(pk=self.a.pk).exists())
    def test_delete_permanently_cascades_owned_content_and_preserves_shared_resources(self):
        group_id=self.a.pk;task_id=self.task.pk;meeting_id=self.meeting.pk
        with patch('core.calendar_service.calendar_adapter') as calendar, patch('core.friendom.http_transport') as provider:
            response=self.post(self.body())
            self.assertEqual(response.status_code,200);calendar.assert_not_called();provider.assert_not_called()
        self.assertFalse(ResearchGroup.objects.filter(pk=group_id).exists())
        for model,query in [(ResearchTask,{'pk':task_id}),(Entry,{'task_id':task_id}),
            (ResearchMeeting,{'pk':meeting_id}),(MeetingCalendarLink,{'meeting_id':meeting_id}),
            (MeetingSyncOperation,{'meeting_id':meeting_id}),(GroupInvitation,{'group_id':group_id}),
            (GroupSpaceBinding,{'group_id':group_id}),(Membership,{'group_id':group_id}),
            (CalendarSharing,{'group_id':group_id})]:
            self.assertFalse(model.objects.filter(**query).exists(),model.__name__)
        self.assertFalse(ResearchTask.assignees.through.objects.filter(researchtask_id=task_id).exists())
        self.assertFalse(ResearchMeeting.participants.through.objects.filter(researchmeeting_id=meeting_id).exists())
        self.assertEqual(InvitationAcceptance.objects.count(),1)
        self.assertTrue(InvitationAcceptance.objects.filter(invitation=self.other_invite).exists())
        self.assertTrue(Entry.objects.filter(pk=self.other_entry.pk).exists())
        self.assertTrue(MeetingCalendarLink.objects.filter(pk=self.other_link.pk,connection=self.connection).exists())
        self.assertTrue(ResearchGroup.objects.filter(pk=self.b.pk).exists())
        self.assertTrue(ResearchTask.objects.filter(pk=self.other_task.pk).exists())
        self.assertTrue(ResearchTask.objects.filter(pk=self.personal.pk).exists())
        self.assertEqual(User.objects.count(),4);self.assertEqual(SpaceIdentity.objects.count(),1)
        self.assertEqual(CalendarConnection.objects.count(),1);self.assertEqual(CalendarCredential.objects.count(),1)
        self.assertTrue(CalendarSharing.objects.filter(user=self.student,group=self.b).exists())
        self.assertEqual(self.client.get('/api/join/'+self.token+'/').status_code,404)
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}/').status_code,404)
        self.assertEqual(self.client.get(f'/api/groups/{group_id}/members/').status_code,404)
        self.assertEqual(self.client.get(f'/api/groups/{group_id}/meetings/').status_code,404)
        self.assertNotIn(group_id,[g['id'] for g in self.client.get('/api/groups/').json()])
        self.assertEqual(GroupActionReceipt.objects.count(),1)
    def test_self_exit_preserves_research_but_revokes_group_access_and_sharing(self):
        self.client.force_login(self.student)
        self.client.get('/api/session/');deadline=self.client.session['_session_expiry']
        self.assertEqual(self.post(self.body('leave')).status_code,200)
        self.assertFalse(Membership.objects.filter(group=self.a,user=self.student).exists())
        self.assertFalse(CalendarSharing.objects.filter(group=self.a,user=self.student).exists())
        self.assertTrue(self.task.assignees.filter(pk=self.student.pk).exists())
        self.assertEqual(Entry.objects.filter(task=self.task).count(),3)
        self.assertTrue(ResearchMeeting.objects.filter(pk=self.meeting.pk).exists())
        self.assertTrue(Membership.objects.filter(group=self.b,user=self.student).exists())
        for path in [f'/api/tasks/{self.task.pk}/',f'/api/groups/{self.a.pk}/members/',f'/api/groups/{self.a.pk}/meetings/',f'/api/groups/{self.a.pk}/calendar/sharing/']:
            self.assertEqual(self.client.get(path).status_code,404,path)
        self.assertEqual(self.client.get('/api/tasks/',{'group_id':self.a.pk}).status_code,404)
        self.assertEqual(self.client.session['_session_expiry'],deadline)
    def test_owner_cannot_leave_ownerless_group(self):
        self.assertEqual(self.post(self.body('leave')).status_code,409)
        self.assertTrue(Membership.objects.filter(group=self.a,user=self.owner).exists())
        self.assertEqual(GroupActionReceipt.objects.count(),0)
    def test_exact_name_explicit_intent_and_payload_are_required(self):
        for body in [self.body(confirm_name='wrong'),self.body(confirm_name=self.a.name+' '),self.body(confirm_permanent=False),self.body(request_key='bad'),{**self.body(),'user_id':self.owner.id}]:
            self.assertEqual(self.post(body).status_code,400)
        self.assertTrue(ResearchGroup.objects.filter(pk=self.a.pk).exists())
    def test_cross_group_actor_and_anonymous_cannot_operate(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.post(self.body()).status_code,404)
        self.assertEqual(self.post(self.body('leave')).status_code,404)
        self.client.logout();self.assertEqual(self.post(self.body()).status_code,403)
        self.assertEqual(GroupActionReceipt.objects.count(),0)
    def test_duplicate_delete_and_actor_key_conflict_are_safe(self):
        body=self.body()
        self.assertEqual(self.post(body).status_code,200)
        self.assertTrue(self.post(body).json()['replayed'])
        self.assertEqual(GroupActionReceipt.objects.count(),1)
        self.client.force_login(self.student)
        self.assertEqual(self.post(body).status_code,403)
        self.assertTrue(ResearchGroup.objects.filter(pk=self.b.pk).exists())
    def test_duplicate_exit_does_not_remove_a_later_rejoined_membership(self):
        self.client.force_login(self.student);body=self.body('leave')
        self.assertEqual(self.post(body).status_code,200)
        self.assertTrue(self.post(body).json()['replayed'])
        Membership.objects.create(group=self.a,user=self.student,role='student')
        self.assertTrue(self.post(body).json()['replayed'])
        self.assertTrue(Membership.objects.filter(group=self.a,user=self.student).exists())
    def test_transaction_failure_restores_every_owned_row(self):
        with patch('core.group_lifecycle.GroupActionReceipt.objects.create',side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaises(RuntimeError):self.post(self.body())
        self.assertTrue(ResearchGroup.objects.filter(pk=self.a.pk).exists())
        self.assertEqual(Entry.objects.filter(task=self.task).count(),3)
        self.assertEqual(InvitationAcceptance.objects.count(),2)
        self.assertEqual(MeetingCalendarLink.objects.count(),2)
        self.assertEqual(GroupActionReceipt.objects.count(),0)
        self.assertEqual(self.client.get('/api/join/'+self.token+'/').status_code,200)
    def test_running_external_write_blocks_delete_without_cancelling_it(self):
        self.operation.status='running';self.operation.save()
        self.assertEqual(self.post(self.body()).status_code,409)
        self.operation.refresh_from_db();self.assertEqual(self.operation.status,'running')
        self.assertTrue(ResearchGroup.objects.filter(pk=self.a.pk).exists())
    def test_external_sync_after_exit_cannot_start_a_remote_write(self):
        self.meeting.organizer=self.student;self.meeting.save()
        self.client.force_login(self.student);self.post(self.body('leave'))
        adapter=SimpleNamespace(write=Mock())
        with self.assertRaises(CalendarError):sync_meeting(self.meeting.pk,self.student,1,adapter=adapter)
        adapter.write.assert_not_called()
    def test_pending_oauth_cannot_join_deleted_group(self):
        # An invitation removed by group deletion cannot be resolved afterwards.
        self.post(self.body())
        from .group_join import active_invitation
        from rest_framework.exceptions import NotFound
        with self.assertRaises(NotFound):active_invitation(self.token)
    def test_csrf_stays_required_for_authenticated_delete(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner)
        self.assertEqual(c.post(f'/api/groups/{self.a.pk}/lifecycle/',self.body(),content_type='application/json').status_code,403)
        self.assertTrue(ResearchGroup.objects.filter(pk=self.a.pk).exists())
    def test_exit_clears_only_this_groups_browser_authorization(self):
        self.client.force_login(self.student)
        s=self.client.session;s['group_oauth_pending']={'group_id':self.a.pk};s['group_oauth_completed']={'group_id':self.a.pk};s['friendom_pending']={'purpose':'global-login'};s.save()
        self.assertEqual(self.post(self.body('leave')).status_code,200)
        self.assertNotIn('group_oauth_pending',self.client.session)
        self.assertNotIn('group_oauth_completed',self.client.session)
        self.assertIn('friendom_pending',self.client.session)
    def test_earlier_join_in_another_browser_cannot_undo_exit_but_new_join_can(self):
        self.client.force_login(self.student)
        host={'HTTP_HOST':'127.0.0.1:8001'}
        frozen={'request_id':'r'*43,'expires_in':1800,'authorization_url':'https://frienden.jyonn.space/oauth/authorize?request_id='+'r'*43}
        profile={'subject':'sermo:123:2','space_id':123,'name':'虚构学生','verified_email':self.student.email}
        def begin():
            with patch('core.group_join.http_transport',return_value=frozen), patch('django.conf.settings.FRIENDEN_SPACE_OAUTH_ENABLED',True):
                r=self.client.post('/api/join/'+self.token+'/',{'consent':True},content_type='application/json',**host)
                self.assertEqual(r.status_code,200)
            return self.client.session['group_oauth_pending']
        def finish(pending):
            with patch('core.group_join.approved_adapter') as factory, patch('django.conf.settings.FRIENDEN_SPACE_OAUTH_ENABLED',True):
                factory.return_value.complete.return_value=profile
                return self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':'c'*43},**host)
        pending=begin()
        other=Client();other.force_login(self.student)
        self.assertEqual(other.post(f'/api/groups/{self.a.pk}/lifecycle/',self.body('leave'),content_type='application/json').status_code,200)
        self.assertIn('join_error',finish(pending).url)
        self.assertFalse(Membership.objects.filter(group=self.a,user=self.student).exists())
        self.assertIn('group='+str(self.a.pk),finish(begin()).url)
        self.assertTrue(Membership.objects.filter(group=self.a,user=self.student).exists())
