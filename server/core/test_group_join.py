import secrets
import time
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase, override_settings, Client
from django.utils import timezone
from django.db import transaction
from .models import User, ResearchGroup, Membership, ResearchTask, GroupSpaceBinding, GroupInvitation, SpaceIdentity, InvitationAcceptance, FriendomIdentity
from .group_join import digest, resolve_user
from .friendom import FriendomError

@override_settings(FRIENDEN_SPACE_OAUTH_ENABLED=True)
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
    def invite(self):
        r=self.client.post(f'/api/groups/{self.a.id}/invitations/',{},content_type='application/json')
        self.assertEqual(r.status_code,201)
        return r.json()
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

    def begin(self,token,user=None):
        self.client.logout()
        if user:self.client.force_login(user)
        with patch('core.group_join.http_transport',return_value={'request_id':'r'*43,'expires_in':1800,'authorization_url':'https://frienden.jyonn.space/oauth/authorize?request_id='+'r'*43}) as transport:
            response=self.client.post('/api/join/'+token+'/',{'consent':True},content_type='application/json',**self.host)
            self.assertEqual(response.status_code,200)
            params=transport.call_args.args[2]
            self.assertEqual(params['space_id'],12)
            self.assertEqual(params['scope'],'profile email')
        return self.client.session['group_oauth_pending']
    def complete(self,pending,**profile):
        base={'subject':'sermo:12:2','space_id':12,'name':'虚构学生','verified_email':'student@example.test','avatar_url':''}
        base.update(profile)
        with patch('core.group_join.approved_adapter') as factory:
            factory.return_value.complete.return_value=base
            return self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':secrets.token_urlsafe(32)},**self.host)
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
    def test_existing_verified_email_reuses_global_account_and_replay_fails(self):
        token=self.invite()['token'];p=self.begin(token)
        response=self.complete(p)
        self.assertIn('group=',response.url)
        self.assertEqual(User.objects.count(),3)
        self.assertEqual(SpaceIdentity.objects.get().user,self.student)
        self.assertEqual(InvitationAcceptance.objects.count(),1)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.student.pk))
        self.assertIn('error',self.complete(p).url)
    def test_new_email_creates_once_and_multi_space_keeps_account(self):
        token=self.invite()['token'];p=self.begin(token)
        self.complete(p,verified_email='new@example.test',subject='sermo:12:9')
        u=User.objects.get(email='new@example.test');self.assertFalse(u.has_usable_password())
        with transaction.atomic():
            linked=resolve_user({'subject':'sermo:13:9','verified_email':'new@example.test','name':'same'},u,13)
        self.assertEqual(linked.pk,u.pk);self.assertEqual(SpaceIdentity.objects.filter(user=u).count(),2)
    def test_wrong_space_unverified_email_and_logged_in_mismatch_fail(self):
        token=self.invite()['token']
        for overrides in [{'space_id':13},{'verified_email':None},{'subject':'sermo:13:2'}]:
            p=self.begin(token);self.assertIn('join_error',self.complete(p,**overrides).url)
        p=self.begin(token,self.other);self.assertIn('join_error',self.complete(p).url)
        self.assertFalse(SpaceIdentity.objects.exists())
    def test_existing_subject_conflict_and_changed_email_do_not_rebind(self):
        SpaceIdentity.objects.create(user=self.student,space_id=12,subject='sermo:12:77')
        token=self.invite()['token'];p=self.begin(token)
        self.assertIn('join_error',self.complete(p).url)
        p=self.begin(token);self.complete(p,subject='sermo:12:77',verified_email='new-address@example.test')
        self.student.refresh_from_db();self.assertEqual(self.student.email,'student@example.test')
        self.assertEqual(SpaceIdentity.objects.get().subject,'sermo:12:77')
    def test_binding_revision_and_revoked_invite_fail_after_start(self):
        token=self.invite()['token'];p=self.begin(token)
        self.binding.revision+=1;self.binding.save()
        self.assertIn('join_error',self.complete(p).url)
        self.assertFalse(SpaceIdentity.objects.exists())
    def test_admin_requires_claim_and_login_matches(self):
        with patch('core.group_join.http_transport',return_value={'request_id':'r'*43,'expires_in':1800,'authorization_url':'https://frienden.jyonn.space/oauth/authorize?request_id='+'r'*43}):
            self.client.post(f'/api/groups/{self.a.id}/space/',{},content_type='application/json',**self.host)
        p=self.client.session['group_oauth_pending']
        self.assertIn('join_error',self.complete(p,verified_email='mentor@example.test',subject='sermo:12:1').url)
        self.assertEqual(GroupSpaceBinding.objects.get().revision,1)
    def test_direct_add_member_is_no_longer_allowed(self):
        self.assertEqual(self.client.post(f'/api/groups/{self.a.id}/members/',{'email':'other@example.test'},content_type='application/json').status_code,400)
        self.assertFalse(Membership.objects.filter(group=self.a,user=self.other).exists())
    def test_join_preserves_existing_absolute_session_deadline(self):
        token=self.invite()['token'];p=self.begin(token,self.student)
        deadline=self.client.session['_session_expiry']
        self.complete(p)
        self.assertEqual(self.client.session['_session_expiry'],deadline)
    @override_settings(FRIENDEN_SPACE_OAUTH_ENABLED=False)
    def test_disabled_contract_cannot_fake_authorization(self):
        self.assertEqual(self.client.post(f'/api/groups/{self.a.id}/space/',{},content_type='application/json',**self.host).status_code,503)

    def test_anonymous_join_requires_csrf(self):
        token=self.invite()['token']
        c=Client(enforce_csrf_checks=True)
        self.assertEqual(c.post('/api/join/'+token+'/',{'consent':True},content_type='application/json',**self.host).status_code,403)
    def test_changed_subject_email_collision_is_never_merged(self):
        SpaceIdentity.objects.create(user=self.other,space_id=12,subject='sermo:12:2')
        p=self.begin(self.invite()['token'])
        self.assertIn('join_error',self.complete(p).url)
        self.assertFalse(InvitationAcceptance.objects.exists())
    def test_admin_success_and_binding_replacement_invalidates_old_invite(self):
        token=self.invite()['token']
        with patch('core.group_join.http_transport',return_value={'request_id':'r'*43,'expires_in':1800,'authorization_url':'https://frienden.jyonn.space/oauth/authorize?request_id='+'r'*43}):
            self.client.post(f'/api/groups/{self.a.id}/space/',{},content_type='application/json',**self.host)
        p=self.client.session['group_oauth_pending']
        r=self.complete(p,verified_email='mentor@example.test',subject='sermo:12:1',space_admin=True)
        self.assertIn('tab=members',r.url)
        self.assertEqual(GroupSpaceBinding.objects.get().revision,2)
        self.assertEqual(self.client.get('/api/join/'+token+'/').status_code,404)
    def test_join_created_identity_can_use_existing_site_frienden_login(self):
        SpaceIdentity.objects.create(user=self.student,space_id=12,subject='sermo:12:2')
        self.client.logout()
        self.client.get('/api/auth/friendom/start/',**self.host)
        pending=self.client.session['friendom_pending']
        with patch('core.friendom.approved_adapter') as factory:
            factory.return_value.complete.return_value={'subject':'sermo:12:2','name':'student','avatar_url':''}
            self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':secrets.token_urlsafe(32)},**self.host)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.student.pk))
        self.assertEqual(User.objects.count(),3)
    def test_duplicate_start_reuses_frozen_request_and_full_registration_window(self):
        token=self.invite()['token'];p=self.begin(token)
        self.assertGreater(p['expires']-time.time(),1700)
        with patch('core.group_join.http_transport') as transport:
            r=self.client.post('/api/join/'+token+'/',{'consent':True},content_type='application/json',**self.host)
        self.assertTrue(r.json()['resumed']);transport.assert_not_called()
        self.assertEqual(self.client.session['group_oauth_pending']['state'],p['state'])
    def test_cancel_keeps_original_invite_and_no_membership_or_provider_exchange(self):
        token=self.invite()['token'];p=self.begin(token)
        with patch('core.group_join.approved_adapter') as factory:
            r=self.client.get('/api/auth/friendom/callback/',{'error':'access_denied','state':p['state']},**self.host)
        self.assertEqual(parse_qs(urlsplit(r.url).query)['invite'],[token])
        self.assertIn('join_error=cancelled',r.url);factory.assert_not_called()
        self.assertFalse(InvitationAcceptance.objects.exists())
        self.assertNotIn('group_oauth_pending',self.client.session)
    def test_expired_and_revoked_invitation_after_start_cannot_join(self):
        for field in ['expires_at','revoked_at']:
            self.client.force_login(self.teacher)
            token=self.invite()['token'];p=self.begin(token)
            GroupInvitation.objects.filter(token_hash=digest(token)).update(**{field:timezone.now()-timedelta(seconds=1)})
            self.assertIn('join_error',self.complete(p).url)
        self.assertFalse(InvitationAcceptance.objects.exists());self.assertFalse(SpaceIdentity.objects.exists())
    def test_expired_pending_returns_original_invite_without_provider_call(self):
        token=self.invite()['token'];p=self.begin(token)
        p['expires']=time.time()-1;s=self.client.session;s['group_oauth_pending']=p;s.save()
        with patch('core.group_join.approved_adapter') as factory:
            r=self.client.get('/api/auth/friendom/callback/',{'state':p['state'],'code':secrets.token_urlsafe(32)},**self.host)
        self.assertIn('join_error=expired',r.url);self.assertIn('invite=',r.url);factory.assert_not_called()
    def test_full_code_exchange_rejects_wrong_state_and_unverified_email(self):
        token=self.invite()['token'];p=self.begin(token)
        with patch('core.friendom.http_transport') as transport:
            r=self.client.get('/api/auth/friendom/callback/',{'state':'wrong','code':secrets.token_urlsafe(32)},**self.host)
            self.assertIn('join_error',r.url);transport.assert_not_called()
        p=self.begin(token)
        with patch('core.friendom.http_transport',side_effect=[{'access_token':'synthetic','token_type':'Bearer','scope':'profile email'},{'sub':'sermo:12:2','space_id':12,'email':'student@example.test','email_verified':False,'nickname':'虚构学生'}]):
            r=self.client.get('/api/auth/friendom/callback/',{'state':p['state'],'code':secrets.token_urlsafe(32)},**self.host)
        self.assertIn('join_error',r.url);self.assertFalse(SpaceIdentity.objects.exists())
    def test_full_code_exchange_joins_existing_account_and_refresh_is_safe(self):
        Membership.objects.filter(group=self.a,user=self.student).delete()
        token=self.invite()['token'];p=self.begin(token)
        with patch('core.friendom.http_transport',side_effect=[{'access_token':'synthetic','token_type':'Bearer','scope':'profile email'},{'sub':'sermo:12:2','space_id':12,'email':'student@example.test','email_verified':True,'nickname':'虚构学生'}]) as transport:
            r=self.client.get('/api/auth/friendom/callback/',{'state':p['state'],'code':secrets.token_urlsafe(32)},**self.host)
            self.assertEqual(transport.call_count,2)
            self.assertEqual(transport.call_args_list[0].args[2]['code_verifier'],p['verifier'])
        self.assertIn('group='+str(self.a.id),r.url)
        self.assertEqual(User.objects.count(),3)
        self.assertEqual(InvitationAcceptance.objects.count(),1)
        self.assertEqual(self.client.get('/api/tasks/',{'group_id':self.a.id}).status_code,200)
        self.assertEqual(Membership.objects.filter(group=self.a,user=self.student).count(),1)
    def test_admin_cancel_returns_to_same_group_and_preserves_binding(self):
        with patch('core.group_join.http_transport',return_value={'request_id':'r'*43,'expires_in':1800,'authorization_url':'https://frienden.jyonn.space/oauth/authorize?request_id='+'r'*43}):
            self.client.post(f'/api/groups/{self.a.id}/space/',{},content_type='application/json',**self.host)
        p=self.client.session['group_oauth_pending']
        r=self.client.get('/api/auth/friendom/callback/',{'error':'access_denied','state':p['state']},**self.host)
        self.assertIn('group='+str(self.a.id),r.url);self.assertIn('tab=members',r.url)
        self.assertEqual(GroupSpaceBinding.objects.get().revision,1)

    def test_reauthentication_of_same_invite_is_idempotent(self):
        token=self.invite()['token']
        self.complete(self.begin(token))
        self.complete(self.begin(token,self.student))
        self.assertEqual(InvitationAcceptance.objects.count(),1)
        self.assertEqual(Membership.objects.filter(group=self.a,user=self.student).count(),1)
        self.assertTrue(Membership.objects.filter(group=self.b,user=self.student).exists())
        self.assertEqual(SpaceIdentity.objects.count(),1)

    def test_duplicate_exact_callback_restores_group_without_exchange_or_session_extension(self):
        token=self.invite()['token'];p=self.begin(token)
        code=secrets.token_urlsafe(32)
        with patch('core.group_join.approved_adapter') as factory:
            factory.return_value.complete.return_value={'subject':'sermo:12:2','space_id':12,'name':'虚构学生','verified_email':'student@example.test'}
            first=self.client.get('/api/auth/friendom/callback/',{'state':p['state'],'code':code},**self.host)
        deadline=self.client.session['_session_expiry']
        with patch('core.group_join.approved_adapter') as factory, patch('core.friendom.approved_adapter') as legacy:
            second=self.client.get('/api/auth/friendom/callback/',{'state':p['state'],'code':code},**self.host)
            self.assertEqual(second.url,first.url);factory.assert_not_called();legacy.assert_not_called()
        self.assertEqual(InvitationAcceptance.objects.count(),1)
        self.assertEqual(self.client.session['_session_expiry'],deadline)
        Membership.objects.filter(group=self.a,user=self.student).delete()
        self.assertIn('error',self.client.get('/api/auth/friendom/callback/',{'state':p['state'],'code':code},**self.host).url)

    @override_settings(FRIENDEN_SPACE_METADATA_ENABLED=True)
    def test_binding_returns_name_slug_and_caches_trusted_metadata(self):
        with patch('core.group_join.space_metadata', return_value={'id':12,'name':'虚构空间','slug':'fictional'}) as lookup:
            response=self.client.get(f'/api/groups/{self.a.id}/space/')
            self.assertEqual(response.json()['space_name'],'虚构空间')
            self.assertEqual(response.json()['space_slug'],'fictional')
            self.assertNotIn('space_id',response.json())
            self.client.get(f'/api/groups/{self.a.id}/space/')
            self.assertEqual(lookup.call_count,1)
    def test_binding_rejects_space_switch_and_renews_original_space(self):
        url=f'/api/groups/{self.a.id}/space/'
        self.assertEqual(self.client.post(url,{'space_id':99},content_type='application/json',**self.host).status_code,400)
        self.binding.expires_at=timezone.now()-timedelta(seconds=1);self.binding.save()
        state=self.client.get(url).json()
        self.assertTrue(state['bound']);self.assertFalse(state['verified'])
        with patch('core.group_join.http_transport',return_value={'request_id':'r'*43,'expires_in':1800,'authorization_url':'https://frienden.jyonn.space/oauth/authorize?request_id='+'r'*43}) as transport:
            self.assertEqual(self.client.post(url,{},content_type='application/json',**self.host).status_code,200)
            self.assertEqual(transport.call_args.args[2]['space_id'],12)
        pending=self.client.session['group_oauth_pending']
        self.assertIn('join_error',self.complete(pending,space_id=99,subject='sermo:99:1',verified_email=self.teacher.email,space_admin=True).url)
        self.binding.refresh_from_db();self.assertEqual(self.binding.space_id,12)
    def test_metadata_resolution_validates_target_and_does_not_authorize(self):
        from .group_join import space_metadata
        frozen={'request_id':'m'*43}
        with patch('core.group_join.http_transport',side_effect=[frozen,{'target_space':{'id':12,'slug':'sample','name':'虚构空间'}},{'body':{'space_id':12,'slug':'sample','name':'虚构空间','official_user':{'avatar_uri':'https://res.6-79.cn/sermo/avatar/space.png'}}}]) as transport:
            self.assertEqual(space_metadata(12)['slug'],'sample')
            self.assertEqual(transport.call_count,3)
            self.assertIn('/spaces/lookup?slug=sample',transport.call_args.args[1])
        with patch('core.group_join.http_transport',side_effect=[frozen,{'target_space':{'id':99,'slug':'other','name':'错误空间'}}]):
            with self.assertRaises(FriendomError):space_metadata(12)
    def test_space_avatar_is_official_space_resource_not_authorizer_avatar(self):
        from .group_join import public_space_metadata, safe_space_avatar
        uri='https://res.6-79.cn/sermo/avatar/space.png'
        with patch('core.group_join.http_transport',return_value={'body':{'space_id':12,'slug':'lab','name':'虚构空间','avatar_uri':'https://evil.test/personal.png','official_user':{'avatar_uri':uri}}}):
            self.assertEqual(public_space_metadata(12,'lab')['avatar_url'],uri)
        for value in ['javascript:alert(1)','https://evil.test/sermo/avatar/x.png','https://res.6-79.cn@evil.test/sermo/avatar/x','http://res.6-79.cn/sermo/avatar/x','https://res.6-79.cn/private/x','https://res.6-79.cn:443/sermo/avatar/x']:
            self.assertEqual(safe_space_avatar(value),'')
    def test_groups_and_binding_share_space_avatar_without_touching_personal_profile(self):
        uri='https://res.6-79.cn/sermo/avatar/space.png'
        self.binding.space_avatar_url=uri;self.binding.space_slug='lab';self.binding.space_name='虚构空间';self.binding.metadata_at=timezone.now();self.binding.save()
        group=next(g for g in self.client.get('/api/groups/').json() if g['id']==self.a.id)
        self.assertEqual(group['space_avatar_url'],uri)
        self.assertEqual(self.client.get(f'/api/groups/{self.a.id}/space/').json()['space_avatar_url'],uri)
        self.teacher.refresh_from_db();self.assertEqual(self.teacher.first_name,'虚构导师')
    @override_settings(FRIENDEN_SPACE_METADATA_ENABLED=True)
    def test_public_metadata_failure_keeps_cached_binding(self):
        self.binding.space_name='虚构空间';self.binding.space_slug='lab';self.binding.save()
        with patch('core.group_join.public_space_metadata',side_effect=FriendomError('Unavailable')):
            response=self.client.get(f'/api/groups/{self.a.id}/space/').json()
        self.assertEqual(response['space_name'],'虚构空间');self.assertEqual(response['space_slug'],'lab')
        self.binding.refresh_from_db();self.assertEqual(self.binding.space_id,12)
    def test_card_is_branded_self_contained_and_has_one_expiry(self):
        import xml.etree.ElementTree as ET
        self.a.name='虚构 & <长名称> '+('研究空间'*10);self.a.save()
        result=self.invite();svg=result['card_svg'];ET.fromstring(svg)
        self.assertIn('M19 29h7',svg)  # Exact path from the actual bench brand asset.
        self.assertEqual(svg.count('有效至'),1);self.assertEqual(svg.count('Frienden 友间'),1)
        self.assertNotIn('<script',svg);self.assertNotIn('href="https:',svg)
        self.assertIn('width="540" height="660"',svg)
    def test_space_lookup_does_not_accept_changed_target(self):
        from .group_join import public_space_metadata
        with patch('core.group_join.http_transport',return_value={'body':{'space_id':13,'slug':'lab','name':'other'}}):
            with self.assertRaises(FriendomError):public_space_metadata(12,'lab')
