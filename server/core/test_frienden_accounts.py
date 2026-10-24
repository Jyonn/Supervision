"""Ordinary-account migration regressions. All accounts and messages are fictional."""
import gzip
import io
import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase, Client, SimpleTestCase, override_settings
from django.utils import timezone
from . import frienden_api as api, chat_service as chat
from .models import User, ResearchGroup, Membership, GroupSpaceBinding, ChatCredential, SpaceIdentity, GroupCreationAttempt, GroupInvitation


class AccountFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=override_settings(CHAT_VAULT_DIR=Path(self.tmp.name)/'vault');self.config.enable();self.addCleanup(self.config.disable)
        self.teacher=User.objects.create_user(username='teacher',email='teacher@example.test',first_name='导师')
        self.student=User.objects.create_user(username='student',email='student@example.test',first_name='学生')
        self.outsider=User.objects.create_user(username='outside',email='outside@example.test')
        self.client.force_login(self.teacher)
        self.calls=[];self.chatrows=[];self.messages=[]
        self.mock=patch('core.frienden_api.call',side_effect=self.provider);self.mock.start();self.addCleanup(self.mock.stop)
        self.key=str(uuid.uuid4())
    def provider(self,method,path,body=None,token=None,query=None):
        self.calls.append((method,path,body,token,query))
        self.assertNotIn('oauth',path)
        space={'space_id':12,'slug':'research','name':'虚构空间','official_user':{'user_id':101,'avatar_uri':''}}
        uid=102 if token=='student-access-token' else 101
        if path=='/spaces/lookup':return space
        if path=='/spaces/join':
            if body['password']=='wrong':raise api.ProviderError('account_invalid',400)
            self.assertEqual(body['new_user_intent'],'check')
            kind='student' if body['name']=='student' else 'teacher'
            return {'space':space,'auth':{'auth':kind+'-access-token','refresh':kind+'-refresh-token'}}
        if path=='/users/me':return {'user_id':uid,'name':'学生' if uid==102 else '官方','official':uid==101}
        if path=='/spaces/me':return space
        if path=='/users/refresh':return {'auth':'teacher-access-token','refresh':'rotated-refresh-token'}
        if path=='/chats/':return self.chatrows
        if path=='/messages/' and method=='GET':return self.messages
        if path=='/messages/' and method=='POST':
            item={'message_id':100,'user':{'user_id':uid,'name':'发送者'},'type':0,'content':body['content'],'created_at':1234567890,'client_message_id':body['client_message_id']}
            self.messages.append(item);return item
        raise AssertionError((method,path))
    def post(self,path,body,client=None):return (client or self.client).post(path,body,content_type='application/json')
    def create(self,**changes):
        body={'name':'虚构研究组','request_key':self.key,'space_slug':'research','account_name':'teacher','password':'synthetic-password'};body.update(changes)
        return self.post('/api/groups/',body)
    def group(self):
        response=self.create();self.assertEqual(response.status_code,201,response.content)
        return ResearchGroup.objects.get(pk=response.json()['group_id'])
    def invite(self,group):
        response=self.post(f'/api/groups/{group.pk}/invitations/',{});self.assertEqual(response.status_code,201,response.content)
        return response.json()['token']
    def joined(self):
        group=self.group();token=self.invite(group);self.client.force_login(self.student)
        response=self.post('/api/join/'+token+'/',{'name':'student','password':'synthetic-password'})
        self.assertEqual(response.status_code,200,response.content)
        return group
    def seed_room(self,group,extra=False):
        members=[{'user_id':101,'name':'官方'},{'user_id':102,'name':'学生'}]
        if extra:members.append({'user_id':999,'name':'外人'})
        self.chatrows=[{'chat_id':15,'members':members,'title':'研究讨论','group':True,'purpose':0,'is_space_group':False}]
        return self.client.get(f'/api/groups/{group.pk}/chat/conversations/').json()
    def test_create_checks_official_and_encrypts_tokens_without_password(self):
        group=self.group();credential=ChatCredential.objects.get()
        self.assertEqual(credential.subject,'sermo:12:101')
        self.assertNotIn('synthetic-password',credential.ciphertext)
        self.assertNotIn('teacher-access-token',credential.ciphertext)
        stored=json.loads(chat.cipher().decrypt(credential.ciphertext.encode()))
        self.assertNotIn('password',stored);self.assertEqual(stored['version'],2)
        self.assertEqual(GroupSpaceBinding.objects.get().admin_subject,credential.subject)
        self.assertEqual(Membership.objects.get(group=group).role,'mentor')
    def test_personal_account_cannot_create_group(self):
        self.assertEqual(self.create(account_name='student').status_code,403)
        self.assertFalse(ResearchGroup.objects.exists());self.assertFalse(ChatCredential.objects.exists())
    def test_wrong_password_leaves_no_local_rows(self):
        self.assertEqual(self.create(password='wrong').status_code,400)
        self.assertFalse(ResearchGroup.objects.exists());self.assertFalse(SpaceIdentity.objects.exists())
    def test_failure_rolls_back_group_identity_and_attempt(self):
        with patch('core.chat_service.cipher',side_effect=api.ProviderError('provider_unavailable')):
            self.assertEqual(self.create().status_code,502)
        for model in (ResearchGroup,Membership,GroupSpaceBinding,SpaceIdentity,GroupCreationAttempt):self.assertFalse(model.objects.exists())
    def test_create_replay_is_idempotent_and_session_bound(self):
        group=self.group();before=len(self.calls)
        self.assertEqual(self.create().json()['group_id'],group.pk);self.assertEqual(len(self.calls),before)
        other=Client();other.force_login(self.teacher)
        body={'name':'虚构研究组','request_key':self.key,'space_slug':'research','account_name':'teacher','password':'synthetic-password'}
        self.assertEqual(self.post('/api/groups/',body,other).status_code,403)
    def test_deleted_group_cannot_be_recreated_by_old_key(self):
        group=self.group();group.delete()
        self.assertEqual(self.create().status_code,404);self.assertFalse(ResearchGroup.objects.exists())
    def test_student_join_requires_supervision_login(self):
        group=self.group();token=self.invite(group);self.client.logout()
        self.assertEqual(self.post('/api/join/'+token+'/',{'name':'student','password':'synthetic-password'}).status_code,403)
        self.assertEqual(Membership.objects.count(),1)
    def test_student_cannot_borrow_official_account(self):
        group=self.group();token=self.invite(group);self.client.force_login(self.student)
        self.assertEqual(self.post('/api/join/'+token+'/',{'name':'teacher','password':'synthetic-password'}).status_code,403)
        self.assertFalse(Membership.objects.filter(user=self.student).exists())
    def test_join_preserves_local_identity_and_session_deadline(self):
        group=self.group();token=self.invite(group);self.client.force_login(self.student)
        session=self.client.session;deadline=timezone.now()+timedelta(days=3);session.set_expiry(deadline);session.save()
        response=self.post('/api/join/'+token+'/',{'name':'student','password':'synthetic-password'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.client.session.get_expiry_date(),deadline)
        self.assertEqual(self.client.session['_auth_user_id'],str(self.student.pk))
        self.assertEqual(self.student.email,'student@example.test')
        self.assertEqual(ChatCredential.objects.get(user=self.student).subject,'sermo:12:102')
    def test_other_local_user_cannot_claim_same_space_identity(self):
        group=self.joined();self.client.force_login(self.outsider)
        token=GroupInvitation.objects.get();from .group_join import digest
        raw='r'*43;token.token_hash=digest(raw);token.save()
        self.assertEqual(self.post('/api/join/'+raw+'/',{'name':'student','password':'synthetic-password'}).status_code,409)
        self.assertFalse(Membership.objects.filter(user=self.outsider).exists())
    def test_expired_and_revoked_invitations_refuse_without_provider_calls(self):
        group=self.group();raw=self.invite(group);before=len(self.calls)
        GroupInvitation.objects.update(revoked_at=timezone.now())
        self.assertEqual(self.post('/api/join/'+raw+'/',{'name':'student','password':'synthetic-password'}).status_code,404)
        self.assertEqual(len(self.calls),before)
    def test_csrf_and_group_membership_are_required(self):
        group=self.group();c=Client(enforce_csrf_checks=True);c.force_login(self.teacher)
        self.assertEqual(self.post(f'/api/groups/{group.pk}/chat/connection/',{'name':'teacher','password':'synthetic-password'},c).status_code,403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(f'/api/groups/{group.pk}/chat/status/').status_code,404)
    def test_existing_oauth_envelope_cannot_be_used(self):
        group=self.group();credential=ChatCredential.objects.get()
        credential.ciphertext=chat.cipher().encrypt(json.dumps({'token':'old-oauth-token'}).encode()).decode();credential.save()
        response=self.client.get(f'/api/groups/{group.pk}/chat/status/')
        self.assertFalse(response.json()['connected'])
    def test_refresh_rotates_encrypted_tokens_and_rechecks_identity(self):
        group=self.group();ChatCredential.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertTrue(self.client.get(f'/api/groups/{group.pk}/chat/status/').json()['connected'])
        stored=json.loads(chat.cipher().decrypt(ChatCredential.objects.get().ciphertext.encode()))
        self.assertEqual(stored['refresh'],'rotated-refresh-token')
    def test_invalid_refresh_identity_erases_credential_after_transaction(self):
        group=self.group();ChatCredential.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        with patch('core.frienden_api.verify',side_effect=api.ProviderError('identity_conflict',409)):
            response=self.client.get(f'/api/groups/{group.pk}/chat/status/')
        self.assertEqual(response.status_code,409)
        self.assertEqual(ChatCredential.objects.get().ciphertext,'')
    def test_provider_revocation_during_send_erases_secret_after_rollback(self):
        group=self.joined();key=self.seed_room(group)['rooms'][0]['key'];original=self.provider
        def provider(method,path,*args,**kwargs):
            if method=='POST' and path=='/messages/':raise api.ProviderError('connection_required',401)
            return original(method,path,*args,**kwargs)
        with patch('core.frienden_api.call',side_effect=provider):
            result=self.post(f'/api/groups/{group.pk}/chat/messages/',{'content':'虚构文字','client_message_id':str(uuid.uuid4()),'room_key':key})
        self.assertEqual(result.status_code,401)
        self.assertEqual(ChatCredential.objects.get(user=self.student).ciphertext,'')
    def test_reconnect_changes_generation_and_invalidates_old_conversation_key(self):
        group=self.joined();key=self.seed_room(group)['rooms'][0]['key']
        response=self.post(f'/api/groups/{group.pk}/chat/connection/',{'name':'student','password':'synthetic-password'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get(f'/api/groups/{group.pk}/chat/messages/',{'room_key':key}).status_code,403)
    def test_binding_rejects_space_switch_fields_before_provider_call(self):
        group=self.group();before=len(self.calls)
        result=self.post(f'/api/groups/{group.pk}/space/',{'space_slug':'other','name':'teacher','password':'synthetic-password'})
        self.assertEqual(result.status_code,400);self.assertEqual(len(self.calls),before)
    def test_disconnect_erases_local_secret_without_remote_logout(self):
        group=self.group();before=len(self.calls)
        response=self.client.delete(f'/api/groups/{group.pk}/chat/connection/',{},content_type='application/json')
        self.assertEqual(response.status_code,200);self.assertEqual(ChatCredential.objects.get().ciphertext,'');self.assertEqual(len(self.calls),before)
    def test_conversations_filter_outside_members_and_space_group(self):
        group=self.joined();self.assertEqual(self.seed_room(group,extra=True)['rooms'],[])
        self.seed_room(group);self.chatrows[0]['is_space_group']=True
        self.assertEqual(self.client.get(f'/api/groups/{group.pk}/chat/conversations/').json()['rooms'],[])
    def test_message_history_send_and_own_credential(self):
        group=self.joined();rooms=self.seed_room(group)['rooms'];key=rooms[0]['key'];cid=str(uuid.uuid4())
        response=self.post(f'/api/groups/{group.pk}/chat/messages/',{'content':'第一条研究讨论','client_message_id':cid,'room_key':key})
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['message']['sender_id'],'102')
        self.assertEqual(self.calls[-1][3],'student-access-token')
        response=self.client.get(f'/api/groups/{group.pk}/chat/messages/',{'room_key':key})
        self.assertEqual(response.json()['messages'][0]['content'],'第一条研究讨论')
    def test_cross_user_room_keys_and_removed_membership_fail(self):
        group=self.joined();key=self.seed_room(group)['rooms'][0]['key']
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(f'/api/groups/{group.pk}/chat/messages/',{'room_key':key}).status_code,403)
        self.client.force_login(self.student);Membership.objects.filter(group=group,user=self.student).delete()
        self.assertEqual(self.client.get(f'/api/groups/{group.pk}/chat/messages/',{'room_key':key}).status_code,404)
    def test_all_oauth_and_advanced_chat_routes_retired(self):
        group=self.group()
        for path in ('/api/auth/friendom/start/','/api/auth/friendom/callback/','/api/auth/friendom/email/start/',f'/api/groups/{group.pk}/chat/collaboration/',f'/api/groups/{group.pk}/chat/events/'):
            self.assertEqual(self.client.get(path).status_code,404,path)


class ProviderContractTests(SimpleTestCase):
    def test_plain_and_general_compact_message_decoding(self):
        raw={'format':'compact-v1','schemas':[['message_id','user'],['name','user_id']], 'records':[[1,'学生',2],[0,17,[-2,0]]],'root':[-1,[-2,1]]}
        self.assertEqual(api.unpack(raw),[{'message_id':17,'user':{'name':'学生','user_id':2}}])
        self.assertEqual(api.unpack([{'message_id':17}]),[{'message_id':17}])
    def test_chat_table_compression_decoding(self):
        raw={'format':'compact-v1','users':{'schemas':[['name','user_id']],'rows':[[0,['学生',2]]]},'chats':{'schemas':[['chat_id','owner','members','last_message']],'rows':[[0,[17,0,[[0,{'joined_at':123}]],{'user':0,'content':'文字'}]]]}}
        result=api.unpack(raw)[0];self.assertEqual(result['members'][0]['user_id'],2);self.assertEqual(result['last_message']['user']['name'],'学生')
    def test_invalid_recursive_compact_ref_is_bounded(self):
        for raw in ({'format':'compact-v1','schemas':[['self']],'records':[[0,[-2,0]]],'root':[-2,0]}, {'format':'compact-v1','schemas':[],'records':[],'root':[-2,99]}):
            with self.assertRaises(api.ProviderError):api.unpack(raw)
    def test_official_claim_and_actual_space_owner_must_both_match(self):
        for official,uid in ((True,5),(False,7)):
            with patch.object(api,'call',side_effect=[{'user_id':uid,'name':'账户','official':official},{'space_id':12,'official_user':{'user_id':7}}]):
                with self.assertRaises(api.ProviderError) as error:api.verify('synthetic-access-token',12,True)
                self.assertEqual(error.exception.code,'official_required')
    def test_wrong_space_refused_even_for_official(self):
        with patch.object(api,'call',side_effect=[{'user_id':7,'name':'账户','official':True},{'space_id':13,'official_user':{'user_id':7}}]):
            with self.assertRaises(api.ProviderError) as error:api.verify('synthetic-access-token',12,True)
            self.assertEqual(error.exception.code,'space_mismatch')
    def test_transport_never_accepts_oauth_or_caller_url(self):
        for path in ('/users/oauth/token','https://other.test/users/me','/users/me?token=secret'):
            with patch.object(api,'build_opener') as opener:
                with self.assertRaises(api.ProviderError):api.call('GET',path)
                opener.assert_not_called()
    def test_gzip_envelope_is_decoded(self):
        class Response(io.BytesIO):
            headers={'Content-Encoding':'gzip'}
        response=Response(gzip.compress(json.dumps({'body':[{'message_id':17}]}).encode()))
        with patch.object(api,'build_opener') as opener:
            opener.return_value.open.return_value=response
            self.assertEqual(api.call('GET','/messages/',token='synthetic-access-token'),[{'message_id':17}])
