"""Fictional provider contract over actual authenticated BFF routes; no live actions."""
import copy
import secrets
import uuid
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit
from django.test import TestCase,override_settings
from django.utils import timezone
from . import test_chat as fixture
from . import chat_service as svc
from .models import ChatCredential,GroupChatRoom,Membership,ResearchGroup,SpaceIdentity

@override_settings(FRIENDEN_CHAT_ENABLED=True,FRIENDEN_SPACE_OAUTH_ENABLED=True,FRIENDOM_CLIENT_ID='supervision-web')
class CollaborationTests(TestCase):
    host=fixture.ChatTests.host
    setUp=fixture.ChatTests.setUp
    url=fixture.ChatTests.url
    post=fixture.ChatTests.post
    msg=fixture.ChatTests.msg
    credential=fixture.ChatTests.credential
    begin=fixture.ChatTests.begin
    callback=fixture.ChatTests.callback
    read=fixture.ChatTests.read
    def provider(self,method,path,body=None,token=None):
        if not hasattr(self,'collab'):
            self.collab={900:self.raw(900,True,[101,102]),901:self.raw(901,False,[101,102])}
            self.friends=[];self.requests=[];self.cap=True
        if path=='/users/oauth/contacts':
            if method=='GET':return {'friends':[{'user':{'user_id':v,'name':'虚构组员'},'request_id':10} for v in self.friends],'requests':copy.deepcopy(self.requests),'groups':[]}
            result={'request_id':10,'peer':{'user_id':body['peer_user_id'],'name':'虚构组员'},'outgoing':True,'status':0};self.requests.append(result);return {'request':result}
        if path.endswith('/respond'):
            row=next(r for r in self.requests if r['request_id']==int(path.split('/')[-2]));row['status']=1 if body['accept'] else 2
            if body['accept']:self.friends.append(row['peer']['user_id'])
            return {'request':copy.deepcopy(row)}
        if path=='/users/oauth/chat/capabilities':return {'create_group':self.cap}
        if path=='/users/oauth/chat/invitations':return {'invitations':[{'chat_id':900,'title':'研究组群','invited_by':None},{'chat_id':999,'title':'外组群','invited_by':None}]}
        if path.startswith('/users/oauth/contacts/search?'):return {'user':{'user_id':102,'name':'虚构学生'},'relationship':None}
        if path=='/users/oauth/chat/direct':return {'chat':copy.deepcopy(self.collab[901])}
        if path=='/users/oauth/chat/groups':
            self.collab[905]=self.raw(905,True,[101]+body['user_ids']);self.collab[905]['title']=body['title'];return {'chat':copy.deepcopy(self.collab[905])}
        if path=='/users/oauth/chat/chats':
            return {'space_id':12,'chats':[{**r,'members':[{'user_id':m['user']['user_id'],'name':m['user']['name']} for m in r['members']]} for r in self.collab.values()]}
        bits=path.split('/')
        if len(bits)>=6 and bits[4]=='chats' and bits[5].isdigit():
            cid=int(bits[5]);row=self.collab[cid]
            if len(bits)==6:return {'chat':copy.deepcopy(row)}
            if bits[-1]=='manage':
                action=body['action']
                if action=='rename':row['title']=body['title']
                if action=='background':row['background_theme']=body['background_theme']
                if action=='remove':row['members']=[m for m in row['members'] if m['user']['user_id']not in body['user_ids']]
                if action=='invite':row['members']+=[self.member(v) for v in body['user_ids']]
                if action=='transfer_owner':
                    for m in row['members']:m['role']=int(m['user']['user_id']==body['peer_user_id'])
                    row['is_owner']=False;row['allowed_actions']=['invite','rename','leave']
                if action=='leave':return {'chat_id':cid,'left':True}
                return {'chat':copy.deepcopy(row)}
            if bits[-1]=='preference':return {'chat_id':cid,'pinned':body['pinned']}
            if bits[-1]=='read':return {'chat_id':cid,'last_read_at':1792300000}
            if bits[-1]=='invitation':return {'chat_id':cid,'accepted':body['accept']}
        return fixture.ChatTests.provider(self,method,path,body,token)
    def member(self,id):return {'user':{'user_id':id,'name':'虚构成员','email':'private@example.test'},'role':int(id==101),'status':1,'joined_at':1792300000}
    def raw(self,id,group,people):return {'chat_id':id,'title':'虚构会话','group':group,'is_space_group':False,'is_owner':group,'background_theme':'','allowed_actions':['invite','remove','transfer_owner','rename','background','mute','unmute']if group else [],'members':[self.member(v) for v in people]}
    def connect(self):
        self.credential();self.provider('GET','/users/oauth/contacts');return self.client.get(self.url('collaboration')).json()
    def action(self,action,**body):return self.post('collaboration',{'action':action,**body})
    def test_directory_contact_request_accept_direct_history_send_events(self):
        data=self.connect();self.assertTrue(data['create_group']);self.assertEqual(len(data['conversations']),2);self.assertEqual(len(data['invitations']),1)
        self.assertEqual(data['peers'][0]['id'],'102');self.assertNotIn('email',str(data))
        self.assertEqual(self.action('search',name='虚构学生').json()['user']['id'],'102')
        self.assertEqual(self.action('friend_request',peer_user_id='102').status_code,200)
        self.assertEqual(self.action('direct',peer_user_id=102).status_code,403)
        self.requests=[{'request_id':11,'peer':{'user_id':102,'name':'虚构学生'},'outgoing':False,'status':0}]
        self.assertEqual(self.action('friend_respond',request_id=11,accept=True).json()['request']['status'],1)
        direct=self.action('direct',peer_user_id='102').json()['conversation'];self.assertFalse(direct['group']);self.assertEqual(direct['allowed_actions'],[])
        key=direct['key'];history=self.client.get(self.url('messages'),{'room_key':key},**self.host);self.assertEqual(history.status_code,200)
        sent=self.post('messages',{'room_key':key,'content':'虚构私聊文字','client_message_id':str(uuid.uuid4())});self.assertEqual(sent.status_code,200)
        self.assertEqual(self.client.get(self.url('events'),{'room_key':key,'after':0},**self.host).status_code,200)
        for action,body in [('read',{}),('pinned',{'pinned':True})]:self.assertEqual(self.action(action,key=key,**body).status_code,200)
        self.assertEqual(self.action('rename',key=key,title='越权').status_code,403)
    def test_group_creation_manage_every_action_and_role_change(self):
        self.connect();created=self.action('create_group',title='虚构新群',user_ids=[]);self.assertEqual(created.status_code,200)
        key=created.json()['conversation']['key']
        for action,body in [('invite',{'user_ids':[102]}),('rename',{'title':'虚构更名'}),('background',{'background_theme':'paper'}),('mute',{'peer_user_id':102,'duration':'10m'}),('unmute',{'peer_user_id':102}),('remove',{'user_ids':[102]}),('invite',{'user_ids':[102]})]:
            result=self.action(action,key=key,**body);self.assertEqual(result.status_code,200,(action,result.json()))
        self.assertEqual(self.action('mute',key=key,peer_user_id=102,duration='permanent').status_code,400)
        self.assertEqual(self.action('background',key=key,background_theme='https://outside.invalid/').status_code,400)
        self.assertEqual(self.action('transfer_owner',key=key,peer_user_id=102).status_code,200)
        self.assertEqual(self.action('remove',key=key,user_ids=[102]).status_code,403)
        self.assertTrue(self.action('leave',key=key).json()['left'])
        self.assertTrue(Membership.objects.filter(group=self.group,user=self.teacher).exists())
    def test_pending_invitations_only_fixed_group_and_explicit_boolean(self):
        data=self.connect();key=data['invitations'][0]['key']
        self.assertEqual(self.action('invitation',key=key,accept=True).status_code,200)
        self.assertEqual(self.action('invitation',key=key,accept=False).status_code,200)
        self.assertEqual(self.action('invitation',key=key,accept='true').status_code,400)
    def test_no_crossgroup_peer_or_signature_or_browser_identity_override(self):
        data=self.connect();key=data['conversations'][0]['key']
        for body in [{'action':'friend_request','peer_user_id':103},{'action':'create_group','user_ids':[103],'title':'bad'}, {'action':'invite','key':key,'user_ids':[103]}, {'action':'rename','key':key,'title':'x','space_id':13}]:
            self.assertIn(self.post('collaboration',body).status_code,(400,403))
        self.assertEqual(self.action('select',key=key+'tamper').status_code,409)
        self.client.force_login(self.student);self.credential(user=self.student)
        self.assertEqual(self.action('select',key=key).status_code,409)
        self.assertEqual(self.action('create_group',title='bad',user_ids=[]).status_code,403)
    def test_capability_disabled_old_narrow_credential_and_removed_roster_fail_closed(self):
        self.connect();self.cap=False
        self.assertFalse(self.client.get(self.url('collaboration')).json()['create_group'])
        self.assertEqual(self.action('create_group',title='bad',user_ids=[]).status_code,403)
        self.collab[900]['members'].append(self.member(103))
        from .collaboration import key_for
        key=key_for(ChatCredential.objects.get(),900)
        self.assertEqual(self.action('select',key=key).status_code,403)
        credential=ChatCredential.objects.get();import json
        raw=json.loads(svc.cipher().decrypt(credential.ciphertext.encode()));raw['scopes']=['profile','email','chat.read','chat.write'];credential.ciphertext=svc.cipher().encrypt(json.dumps(raw).encode()).decode();credential.save()
        self.assertEqual(self.client.get(self.url('collaboration')).status_code,401)
        credential.refresh_from_db();self.assertEqual(credential.ciphertext,'')
    def test_provider_failure_does_not_auto_retry_or_fabricate_success(self):
        self.connect();before=len(self.calls)
        with patch('core.chat_service.remote',side_effect=svc.ChatError('send_uncertain',502)) as remote:
            response=self.action('friend_request',peer_user_id=102);self.assertEqual(response.status_code,502);self.assertEqual(remote.call_count,1)
    def test_teacher_bootstrap_and_student_join_combined_tokens_server_only(self):
        self.binding.expires_at=timezone.now()+timedelta(hours=1);self.binding.save()
        def transport(method,url,body,headers):
            path=urlsplit(url).path
            if path.endswith('/userinfo'):
                return {'sub':'sermo:12:101' if self.client.session.get('group_oauth_pending',{}).get('purpose')=='space_admin' else 'sermo:12:102','space_id':12,'space_admin':True,'email':self.teacher.email if self.client.session.get('group_oauth_pending',{}).get('purpose')=='space_admin' else self.student.email,'email_verified':True}
            return self.provider(method,path,body)
        with patch('core.group_join.http_transport',side_effect=transport) as frozen,patch('core.friendom.http_transport',side_effect=transport):
            result=self.client.post('/api/groups/',{'name':'虚构组合组','request_key':str(uuid.uuid4())},content_type='application/json',**self.host)
            self.assertEqual(result.status_code,200);pending=self.client.session['group_oauth_pending'];self.assertEqual(pending['scopes'],list(svc.SCOPES))
            params=frozen.call_args.args[2];self.assertNotIn('space_id',params);self.assertEqual(params['purpose'],'space_admin')
            result=self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':'c'*43},**self.host)
            self.assertIn('created=1',result.url);new=ResearchGroup.objects.get(name='虚构组合组');self.assertTrue(ChatCredential.objects.filter(group=new,user=self.teacher).exists())
            invite=self.client.post(f'/api/groups/{self.group.pk}/invitations/',{},content_type='application/json').json()['token']
            self.client.force_login(self.student);self.client.post('/api/join/'+invite+'/',{'consent':True},content_type='application/json',**self.host)
            pending=self.client.session['group_oauth_pending'];self.assertEqual(pending['scopes'],list(svc.SCOPES));self.assertEqual(frozen.call_args.args[2]['space_id'],12)
            result=self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':'c'*43},**self.host)
            self.assertNotIn('error',result.url);self.assertTrue(ChatCredential.objects.filter(group=self.group,user=self.student).exists());self.assertNotIn('t'*43,str(self.client.session.items()))
