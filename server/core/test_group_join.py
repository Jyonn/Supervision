from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from .models import User, ResearchGroup, Membership, ResearchTask, GroupSpaceBinding, GroupInvitation
from .group_join import digest

class GroupWorkspaceTests(TestCase):
    host={'HTTP_HOST':'127.0.0.1:8001'}
    def setUp(self):
        self.teacher=User.objects.create_user(username='mentor',email='mentor@example.test',first_name='虚构导师')
        self.student=User.objects.create_user(username='student',email='student@example.test',first_name='虚构学生')
        self.other=User.objects.create_user(username='other',email='other@example.test')
        self.a=ResearchGroup.objects.create(name='虚构甲组',owner=self.teacher)
        self.b=ResearchGroup.objects.create(name='虚构乙组',owner=self.teacher)
        for g in [self.a,self.b]:
            Membership.objects.create(group=g,user=self.teacher,role='mentor')
            Membership.objects.create(group=g,user=self.student,role='student')
        self.task=ResearchTask.objects.create(group=self.a,creator=self.teacher,title='甲组任务')
        self.task.assignees.add(self.student)
        self.task_b=ResearchTask.objects.create(group=self.b,creator=self.teacher,title='乙组任务')
        self.task_b.assignees.add(self.student)
        self.client.force_login(self.teacher)
        self.binding=GroupSpaceBinding.objects.create(group=self.a,space_id=12,admin_subject='sermo:12:1',verified_at=timezone.now(),expires_at=timezone.now()+timedelta(days=1))

        import tempfile
        from pathlib import Path
        from . import chat_service
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        cfg=override_settings(CHAT_VAULT_DIR=Path(self.tmp.name)/'vault');cfg.enable();self.addCleanup(cfg.disable)
        from django.db import transaction
        with transaction.atomic():
            chat_service.store(self.teacher,self.a,{'space_id':12,'user_id':1,'name':'官方','access':'synthetic-access-token','refresh':'synthetic-refresh-token'})
        provider=patch('core.frienden_api.verify',return_value={'user_id':1,'space_id':12,'name':'官方'});provider.start();self.addCleanup(provider.stop)
    def test_join_card_reports_actual_group_count_for_anonymous_and_signed_in(self):
        token=self.invite()['token']
        for authenticated in (False,True):
            self.client.logout()
            if authenticated:self.client.force_login(self.student)
            result=self.client.get('/api/join/'+token+'/').json()
            self.assertEqual(result['group_name'],self.a.name)
            self.assertEqual(result['member_count'],2)
        Membership.objects.create(group=self.a,user=self.other,role='student')
        self.assertEqual(self.client.get('/api/join/'+token+'/').json()['member_count'],3)
        self.assertEqual(self.client.get('/api/join/not-valid/').status_code,404)

    def test_scoped_tasks_counts_and_removed_membership(self):
        self.client.force_login(self.student)
        r=self.client.get('/api/tasks/',{'group_id':self.a.id})
        self.assertEqual([x['id'] for x in r.json()],[self.task.id])
        self.assertEqual(self.client.get(f'/api/tasks/{self.task.id}/',{'group_id':self.b.id}).status_code,404)
        Membership.objects.filter(group=self.a,user=self.student).delete()
        self.assertEqual(self.client.get('/api/tasks/',{'group_id':self.a.id}).status_code,404)
        self.assertEqual(self.client.get(f'/api/tasks/{self.task.id}/').status_code,404)

    def test_invitation_gate_card_hash_and_revocation(self):
        self.binding.active=False;self.binding.save()
        self.assertEqual(self.client.post(f'/api/groups/{self.a.id}/invitations/',{},content_type='application/json').status_code,400)
        self.binding.active=True;self.binding.save()
        r=self.invite();self.assertIn('<svg',r['card_svg']);self.assertIn('扫码加入',r['card_svg'])
        self.assertEqual(GroupInvitation.objects.get().token_hash,digest(r['token']))
        self.assertNotIn(r['token'],GroupInvitation.objects.get().token_hash)
        self.client.delete(f'/api/groups/{self.a.id}/invitations/',{'id':r['id']},content_type='application/json')
        self.assertEqual(self.client.get('/api/join/'+r['token']+'/').status_code,404)

    def test_direct_add_member_is_no_longer_allowed(self):
        self.assertEqual(self.client.post(f'/api/groups/{self.a.id}/members/',{'email':'other@example.test'},content_type='application/json').status_code,400)
        self.assertFalse(Membership.objects.filter(group=self.a,user=self.other).exists())

    def test_anonymous_join_requires_csrf(self):
        token=self.invite()['token']
        c=Client(enforce_csrf_checks=True)
        self.assertEqual(c.post('/api/join/'+token+'/',{'consent':True},content_type='application/json',**self.host).status_code,403)

    def test_groups_and_binding_share_space_avatar_without_touching_personal_profile(self):
        uri='https://res.6-79.cn/sermo/avatar/space.png'
        self.binding.space_avatar_url=uri;self.binding.space_slug='lab';self.binding.space_name='虚构空间';self.binding.metadata_at=timezone.now();self.binding.save()
        group=next(g for g in self.client.get('/api/groups/').json() if g['id']==self.a.id)
        self.assertEqual(group['space_avatar_url'],uri)
        self.assertEqual(self.client.get(f'/api/groups/{self.a.id}/space/').json()['space_avatar_url'],uri)
        self.teacher.refresh_from_db();self.assertEqual(self.teacher.first_name,'虚构导师')

    def test_card_is_branded_self_contained_and_has_one_expiry(self):
        import xml.etree.ElementTree as ET
        self.a.name='虚构 & <长名称> '+('研究空间'*10);self.a.save()
        result=self.invite();svg=result['card_svg'];ET.fromstring(svg)
        self.assertIn('M19 29h7',svg)  # Exact path from the actual bench brand asset.
        self.assertEqual(svg.count('有效至'),1);self.assertEqual(svg.count('Frienden 友间'),1)
        self.assertNotIn('<script',svg);self.assertNotIn('href="https:',svg)
        self.assertIn('width="540" height="660"',svg)

    def invite(self):
        response=self.client.post(f'/api/groups/{self.a.pk}/invitations/',{},content_type='application/json')
        self.assertEqual(response.status_code,201,response.content)
        return response.json()
