"""Fixed-space delegated-chat contract tests; only fictional users and a fake provider."""
import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, override_settings, Client
from django.utils import timezone
from . import chat_service as svc
from .models import (User, ResearchGroup, Membership, GroupSpaceBinding, SpaceIdentity,
                     ChatCredential, ChatAuthorizationAttempt, GroupChatRoom)

@override_settings(FRIENDEN_CHAT_ENABLED=True,FRIENDEN_SPACE_OAUTH_ENABLED=True,FRIENDOM_CLIENT_ID='supervision-web')
class ChatTests(TestCase):
    host={'HTTP_HOST':'127.0.0.1:8001'}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.override=override_settings(CHAT_VAULT_DIR=Path(self.tmp.name)/'vault');self.override.enable();self.addCleanup(self.override.disable)
        self.teacher=User.objects.create_user(username='fictional-mentor',email='mentor@example.test')
        self.student=User.objects.create_user(username='fictional-student',email='student@example.test')
        self.other=User.objects.create_user(username='fictional-other',email='other@example.test')
        self.group=ResearchGroup.objects.create(owner=self.teacher,name='虚构研究组')
        self.second=ResearchGroup.objects.create(owner=self.other,name='另一虚构组')
        for user,role in [(self.teacher,'mentor'),(self.student,'student')]:Membership.objects.create(group=self.group,user=user,role=role)
        Membership.objects.create(group=self.second,user=self.other,role='mentor')
        self.binding=GroupSpaceBinding.objects.create(group=self.group,space_id=12,admin_subject='sermo:12:101',verified_at=timezone.now(),expires_at=timezone.now()-timedelta(hours=1))
        for user,external in [(self.teacher,101),(self.student,102),(self.other,103)]:SpaceIdentity.objects.create(user=user,space_id=12,subject=f'sermo:12:{external}')
        self.room=GroupChatRoom.objects.create(group=self.group,space_id=12,provider_chat_id=900,title='虚构讨论群',configured_by=self.teacher)
        self.sent={};self.calls=[];self.profile_overrides={};self.token_overrides={}
        self.client.force_login(self.teacher)
        self.patch=patch('core.chat_service.transport',side_effect=self.provider);self.patch.start();self.addCleanup(self.patch.stop)
    def url(self,route,group=None):return f'/api/groups/{group or self.group.pk}/chat/{route}/'
    def post(self,route,body,client=None):return (client or self.client).post(self.url(route),body,content_type='application/json',**self.host)
    def msg(self,id=11,sender=101,content='虚构消息',key=''):
        return {'message_id':id,'user':{'user_id':sender,'name':'虚构成员','email':'must-not-leak@example.test'},'type':0,'content':content,'created_at':1792300000,'client_message_id':key,'media_url':'https://private.invalid/never-expose'}
    def provider(self,method,path,body=None,token=None):
        self.calls.append((method,path,body,token))
        if path=='/users/oauth/requests':return {'request_id':'r'*43,'expires_in':1800,'authorization_url':'https://frienden.jyonn.space/oauth/authorize?request_id='+'r'*43}
        if path=='/users/oauth/token':return {'access_token':'t'*43,'token_type':'Bearer','expires_in':3600,'scope':' '.join(svc.SCOPES),**self.token_overrides}
        if path=='/users/oauth/userinfo':return {'sub':'sermo:12:101','space_id':12,'email':self.teacher.email,'email_verified':True,**self.profile_overrides}
        if path=='/users/oauth/revoke':return {'revoked':True}
        if path.endswith('/chats'):return {'space_id':12,'chats':[
            {'chat_id':900,'title':'虚构讨论群','group':True,'is_space_group':False,'members':[{'user_id':101,'name':'导师'},{'user_id':102,'name':'学生'}]},
            {'chat_id':901,'title':'私人会话','group':False,'is_space_group':False,'members':[{'user_id':101,'name':'导师'}]},
            {'chat_id':902,'title':'其他组','group':True,'is_space_group':False,'members':[{'user_id':101,'name':'导师'},{'user_id':103,'name':'其他组员'}]},
            {'chat_id':903,'title':'整个空间','group':True,'is_space_group':True,'members':[{'user_id':101,'name':'导师'}]}]}
        if method=='POST' and path.endswith('/messages'):
            key=body['client_message_id'];self.sent.setdefault(key,self.msg(13,content=body['content'],key=key));return {'message':self.sent[key]}
        if '/messages?' in path:return {'messages':[self.msg()],'has_more':False,'next_before':11}
        if '/events?' in path:return {'events':[{'event_id':1,'message_id':11,'type':'message.hidden'}],'has_more':False,'next_after':1}
        raise AssertionError('Unexpected fixed provider route')
    def credential(self,user=None,group=None,subject=None):
        user=user or self.teacher;group=group or self.group;subject=subject or f'sermo:12:{101 if user==self.teacher else 102}'
        generation=uuid.uuid4();payload={'token':'t'*43,'user':user.pk,'group':group.pk,'space':12,'subject':subject,'generation':str(generation)}
        value=svc.cipher(create=True).encrypt(json.dumps(payload).encode()).decode()
        return ChatCredential.objects.create(user=user,group=group,space_id=12,subject=subject,generation=generation,ciphertext=value,expires_at=timezone.now()+timedelta(hours=1))
    def begin(self):
        result=self.post('connection',{'consent':True});self.assertEqual(result.status_code,200)
        return self.client.session['chat_oauth_pending']
    def callback(self,pending,**extra):
        params={'state':pending['state'],'code':'c'*43,**extra}
        return self.client.get('/api/auth/friendom/callback/',params,**self.host),params
    def read(self,route='messages',**params):
        return self.client.get(self.url(route),{'room_key':str(self.room.generation),**params},**self.host)
    @override_settings(FRIENDEN_CHAT_ENABLED=False)
    def test_disabled_no_provider_or_credential_or_vault_access(self):
        self.assertFalse(self.client.get(self.url('status')).json()['enabled'])
        self.assertEqual(self.post('connection',{'consent':True}).status_code,503)
        self.assertFalse(ChatCredential.objects.exists());self.assertEqual(self.calls,[])
        self.assertFalse((Path(self.tmp.name)/'vault').exists())
    def test_authorization_is_exact_fixed_space_and_backend_only(self):
        pending=self.begin();body=self.calls[0][2]
        self.assertEqual(body['scope'],'profile email chat.read chat.write');self.assertEqual(body['space_id'],12);self.assertEqual(body['purpose'],'join')
        response,params=self.callback(pending);self.assertIn('chat=connected',response.url)
        credential=ChatCredential.objects.get();self.assertNotIn('t'*43,credential.ciphertext)
        self.assertNotIn('t'*43,str(self.client.session.items()));self.assertNotIn('t'*43,response.url)
        status=self.client.get(self.url('status')).json();self.assertTrue(status['connected']);self.assertNotIn('token',status)
        before=len(self.calls);again=self.client.get('/api/auth/friendom/callback/',params,**self.host)
        self.assertIn('chat=connected',again.url);self.assertEqual(len(self.calls),before)
    def test_cancel_wrong_state_wrong_space_and_identity_cannot_replace_credential(self):
        original=self.credential();ciphertext=original.ciphertext
        for overrides,query in [({}, {'error':'access_denied'}),({}, {'state':'wrong'}),({'space_id':13},{}),({'sub':'sermo:12:102'},{}),({'email_verified':False},{})]:
            self.profile_overrides=overrides;pending=self.begin();result,_=self.callback(pending,**query)
            self.assertIn('chat_error',result.url);original.refresh_from_db();self.assertEqual(original.ciphertext,ciphertext)
        self.assertEqual(ChatCredential.objects.count(),1)
    def test_partial_extra_scope_and_invalid_expiry_fail(self):
        for override in [{'scope':'profile email chat.read'},{'scope':'profile email chat.read chat.write phone'},{'expires_in':True}]:
            self.token_overrides=override;pending=self.begin();result,_=self.callback(pending)
            self.assertIn('chat_error',result.url);self.assertFalse(ChatCredential.objects.exists())
    def test_other_session_and_expired_attempt_fail_without_token_exchange(self):
        pending=self.begin();other=Client();other.force_login(self.teacher)
        session=other.session;session['chat_oauth_pending']=pending;session.save()
        result=other.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':'c'*43},**self.host)
        self.assertIn('chat_error',result.url);self.assertFalse(ChatCredential.objects.exists())
        pending=self.begin();ChatAuthorizationAttempt.objects.filter(pk=pending['attempt']).update(expires_at=timezone.now()-timedelta(seconds=1))
        result,_=self.callback(pending);self.assertIn('chat_error',result.url)
        self.assertFalse(any(call[1]=='/users/oauth/token' for call in self.calls))
    def test_expiry_clears_ciphertext(self):
        value=self.credential();value.expires_at=timezone.now()-timedelta(seconds=1);value.save()
        self.assertFalse(self.client.get(self.url('status')).json()['connected']);value.refresh_from_db();self.assertEqual(value.ciphertext,'')
    def test_credential_row_swap_cannot_cross_user_or_group(self):
        value=self.credential();value.user=self.student;value.subject='sermo:12:102';value.save()
        self.client.force_login(self.student);self.assertEqual(self.read().status_code,401)
        value.refresh_from_db();self.assertEqual(value.ciphertext,'')
    def test_candidate_list_excludes_private_other_group_and_space_wide_chats(self):
        self.credential();result=self.client.get(self.url('room')).json()
        self.assertEqual([row['id'] for row in result['rooms']],['900'])
        self.assertEqual(self.post('room',{'chat_id':'902'}).status_code,403)
        self.assertEqual(self.post('room',{'chat_id':'900','extra':True}).status_code,400)
    def test_student_uses_own_credential_and_cannot_configure(self):
        self.credential();self.client.force_login(self.student)
        self.assertEqual(self.read().status_code,401);self.credential(self.student)
        self.assertEqual(self.read().status_code,200);self.assertEqual(self.post('room',{'chat_id':'900'}).status_code,403)
    def test_history_and_events_strip_private_fields_and_obey_current_visibility(self):
        self.credential();data=self.read().json();self.assertEqual(data['messages'][0]['id'],'11')
        self.assertNotIn('email',json.dumps(data));self.assertNotIn('private.invalid',json.dumps(data))
        event=self.read('events',after=0).json()['events'][0];self.assertIsNone(event['message'])
        self.assertEqual(event['message_id'],'11');self.assertEqual(data['viewer_id'],str(self.teacher.pk))
    def test_send_uses_original_idempotency_key_and_requires_room_generation(self):
        self.credential();key=str(uuid.uuid4());body={'content':'虚构发送','client_message_id':key,'room_key':str(self.room.generation)}
        first=self.post('messages',body);second=self.post('messages',body)
        self.assertEqual(first.status_code,200);self.assertEqual(second.json()['message']['id'],first.json()['message']['id']);self.assertEqual(len(self.sent),1)
        self.assertEqual(self.post('messages',{**body,'room_key':str(uuid.uuid4())}).status_code,409)
        self.assertEqual(self.post('messages',{**body,'content':'x'*513}).status_code,400)
        self.assertEqual(self.post('messages',{**body,'sender_id':102}).status_code,400)
    def test_provider_revoke_clears_even_when_send_transaction_rolls_back(self):
        value=self.credential();original=self.provider
        def revoked(method,path,body=None,token=None):
            if method=='POST' and path.endswith('/messages'):raise svc.ChatError('authorization_required',401)
            return original(method,path,body,token)
        with patch('core.chat_service.transport',side_effect=revoked):
            result=self.post('messages',{'content':'虚构','client_message_id':str(uuid.uuid4()),'room_key':str(self.room.generation)})
        self.assertEqual(result.status_code,401);value.refresh_from_db();self.assertEqual(value.ciphertext,'')
    def test_late_read_after_exit_does_not_return_history(self):
        self.credential();original=self.provider
        def leave(method,path,body=None,token=None):
            result=original(method,path,body,token)
            if '/messages?' in path:Membership.objects.filter(group=self.group,user=self.teacher).delete()
            return result
        with patch('core.chat_service.transport',side_effect=leave):self.assertEqual(self.read().status_code,404)
    def test_external_member_change_and_stale_room_key_block_read(self):
        self.credential();Membership.objects.filter(group=self.group,user=self.student).delete()
        self.assertEqual(self.read().status_code,403)
        self.assertEqual(self.read(room_key=str(uuid.uuid4())).status_code,409)
    def test_disconnect_clears_locally_and_reports_uncertain_provider_revoke(self):
        value=self.credential()
        with patch('core.chat_service.transport',side_effect=svc.ChatError('provider_unavailable',502)):
            result=self.client.delete(self.url('connection'),{},content_type='application/json')
        self.assertEqual(result.status_code,200);self.assertFalse(result.json()['provider_revoked'])
        value.refresh_from_db();self.assertEqual(value.ciphertext,'')
    def test_auth_csrf_and_cross_group_refused(self):
        self.credential();client=Client(enforce_csrf_checks=True);client.force_login(self.teacher)
        self.assertEqual(self.post('messages',{},client).status_code,403)
        self.assertEqual(self.client.get(self.url('status',self.second.pk)).status_code,404)
        self.client.logout();self.assertEqual(self.read().status_code,403)
    def test_transport_rejects_unapproved_routes_without_network(self):
        self.patch.stop()
        for path in ['https://evil.test/users/oauth/chat/chats','/messages/','/users/oauth/chat/chats/1/preference','/users/oauth/chat/chats/1/messages?after=1&after=2']:
            with self.assertRaises(svc.ChatError):svc.transport('GET',path)
        self.patch.start()
