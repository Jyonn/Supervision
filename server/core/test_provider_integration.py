"""Opt-in BFF → actual Frienden adapters with separate SQLite in-memory processes."""
import json,os,subprocess,tempfile,uuid,unittest
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.utils import timezone
from datetime import timedelta
from . import chat_service as svc
from .models import User,ResearchGroup,Membership,GroupSpaceBinding,SpaceIdentity,ChatCredential

@unittest.skipUnless(os.environ.get('ABANDON_PROVIDER_FIXTURE')=='1','opt-in actual provider fixture')
@override_settings(FRIENDEN_CHAT_ENABLED=True,FRIENDEN_SPACE_OAUTH_ENABLED=True,FRIENDOM_CLIENT_ID='supervision-web')
class ProviderIntegrationTests(TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.config=override_settings(CHAT_VAULT_DIR=Path(self.tmp.name)/'vault');self.config.enable();self.addCleanup(self.config.disable)
  env={**os.environ,'ABANDON_PROVIDER_SOURCE':os.environ['ABANDON_PROVIDER_SOURCE']}
  script=Path(__file__).resolve().parents[2]/'scripts/provider_fixture_bridge.py'
  self.log=open(Path(self.tmp.name)/'provider.log','w+');self.addCleanup(self.log.close)
  self.bridge=subprocess.Popen([os.environ['ABANDON_PROVIDER_PYTHON'],str(script)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,cwd=self.tmp.name,env=env)
  self.addCleanup(self.cleanup)
  line=self.bridge.stdout.readline()
  if not line:
   self.log.seek(0);raise AssertionError('Fixture initialization failed: '+self.log.read()[-2000:])
  self.info=json.loads(line);self.assertTrue(self.info['ready'])
  self.scope=self.info['space'];self.owner=User.objects.create_user(username='fixture-owner',email='verified@example.test');self.peer=User.objects.create_user(username='fixture-peer',email='fixture0@example.test')
  self.group=ResearchGroup.objects.create(owner=self.owner,name='虚构联调研究组')
  for user,role,key in [(self.owner,'mentor','owner'),(self.peer,'student','peer')]:
   Membership.objects.create(group=self.group,user=user,role=role);SpaceIdentity.objects.create(user=user,space_id=self.scope,subject=f"sermo:{self.scope}:{self.info['users'][key]}")
  GroupSpaceBinding.objects.create(group=self.group,space_id=self.scope,admin_subject=f"sermo:{self.scope}:{self.info['users']['admin']}",verified_at=timezone.now(),expires_at=timezone.now()+timedelta(hours=1))
  self.client.force_login(self.owner)
  self.transport_patch=patch('core.chat_service.transport',side_effect=self.transport);self.transport_patch.start();self.addCleanup(self.transport_patch.stop)
 def cleanup(self):
  self.bridge.terminate()
  try:self.bridge.wait(timeout=5)
  except subprocess.TimeoutExpired:self.bridge.kill();self.bridge.wait()
  self.bridge.stdin.close();self.bridge.stdout.close()
 def rpc(self,data):
  self.bridge.stdin.write(json.dumps(data)+'\n');self.bridge.stdin.flush();line=self.bridge.stdout.readline()
  self.assertTrue(line,'provider bridge closed');result=json.loads(line);self.assertNotIn('bridge_error',result);return result
 def transport(self,method,path,body=None,token=None):
  result=self.rpc({'method':method,'path':path,'body':body,'token':token})
  if result['status']>=400:raise svc.ChatError('authorization_required' if result['status']==401 else 'access_denied',result['status'])
  return result['data']
 def url(self,path):return f'/api/groups/{self.group.pk}/chat/{path}/'
 def post(self,route,body):return self.client.post(self.url(route),body,content_type='application/json',HTTP_HOST='127.0.0.1:8001')
 def action(self,action,**body):
  result=self.post('collaboration',{'action':action,**body});self.assertEqual(result.status_code,200,(action,result.json()));return result.json()
 def consent(self,user,actor):
  self.client.force_login(user);self.rpc({'command':'actor','actor':actor})
  start=self.post('connection',{'consent':True});self.assertEqual(start.status_code,200,start.content)
  pending=self.client.session['chat_oauth_pending'];rid=start.json()['authorize_url'].split('request_id=')[1]
  code=self.rpc({'command':'consent','request_id':rid})['code']
  result=self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':code},HTTP_HOST='127.0.0.1:8001');self.assertIn('chat=connected',result.url)
 def test_real_provider_friendship_private_chat_and_all_group_actions(self):
  self.consent(self.owner,'owner');peer=self.info['users']['peer'];own=self.info['users']['owner']
  request=self.action('friend_request',peer_user_id=peer)['request']
  self.consent(self.peer,'peer');self.action('friend_respond',request_id=request['id'],accept=True)
  self.client.force_login(self.owner)
  private=self.action('direct',peer_user_id=peer)['conversation'];key=private['key']
  sent=self.post('messages',{'room_key':key,'content':'仅内存虚构联调文字','client_message_id':str(uuid.uuid4())});self.assertEqual(sent.status_code,200,sent.content)
  self.assertEqual(self.client.get(self.url('messages'),{'room_key':key}).json()['messages'][0]['content'],'仅内存虚构联调文字')
  self.assertEqual(self.client.get(self.url('events'),{'room_key':key,'after':0}).status_code,200)
  self.action('read',key=key);self.action('pinned',key=key,pinned=True)
  group=self.action('create_group',title='仅内存联调群',user_ids=[])['conversation'];key=group['key']
  self.action('invite',key=key,user_ids=[peer]);self.action('rename',key=key,title='虚构改名')
  self.action('background',key=key,background_theme='');self.action('mute',key=key,peer_user_id=peer,duration='10s');self.action('unmute',key=key,peer_user_id=peer)
  self.action('remove',key=key,user_ids=[peer]);self.action('invite',key=key,user_ids=[peer]);self.action('transfer_owner',key=key,peer_user_id=peer)
  denied=self.post('collaboration',{'action':'remove','key':key,'user_ids':[peer]});self.assertEqual(denied.status_code,403)
  self.action('leave',key=key)
  # Fresh provider checks deny even a previously valid signed selection.
  self.assertEqual(self.post('collaboration',{'action':'select','key':key}).status_code,403)
  self.assertEqual(ChatCredential.objects.count(),2)

 def test_actual_provider_teacher_bootstrap_and_student_invitation_combined_consent(self):
  admin=User.objects.create_user(username='fictional-admin',email='fixture1@example.test');self.client.force_login(admin)
  def request_transport(method,url,body,headers):
   return self.transport(method,urlsplit(url).path+('?' + urlsplit(url).query if urlsplit(url).query else ''),body,headers.get('Authorization','').removeprefix('Bearer ')or None)
  from urllib.parse import urlsplit
  with patch('core.group_join.http_transport',side_effect=request_transport),patch('core.friendom.http_transport',side_effect=request_transport):
   start=self.client.post('/api/groups/',{'name':'虚构管理员组合建组','request_key':str(uuid.uuid4())},content_type='application/json',HTTP_HOST='127.0.0.1:8001')
   self.assertEqual(start.status_code,200,start.content);pending=self.client.session['group_oauth_pending'];self.assertEqual(pending['scopes'],list(svc.SCOPES));self.assertNotIn('space_id',pending)
   rid=start.json()['authorize_url'].split('request_id=')[1];self.rpc({'command':'actor','actor':'admin'});code=self.rpc({'command':'consent','request_id':rid})['code']
   result=self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':code},HTTP_HOST='127.0.0.1:8001');self.assertIn('created=1',result.url)
   group=ResearchGroup.objects.get(name='虚构管理员组合建组');self.assertTrue(ChatCredential.objects.filter(group=group,user=admin).exists())
   invitation=self.client.post(f'/api/groups/{group.pk}/invitations/',{},content_type='application/json').json()['token']
   self.client.force_login(self.peer);self.rpc({'command':'actor','actor':'peer'})
   start=self.client.post('/api/join/'+invitation+'/',{'consent':True},content_type='application/json',HTTP_HOST='127.0.0.1:8001');self.assertEqual(start.status_code,200)
   pending=self.client.session['group_oauth_pending'];self.assertEqual(pending['space_id'],self.scope);self.assertEqual(pending['scopes'],list(svc.SCOPES))
   code=self.rpc({'command':'consent','request_id':start.json()['authorize_url'].split('request_id=')[1]})['code']
   result=self.client.get('/api/auth/friendom/callback/',{'state':pending['state'],'code':code},HTTP_HOST='127.0.0.1:8001');self.assertNotIn('error',result.url)
   self.assertTrue(Membership.objects.filter(group=group,user=self.peer).exists());self.assertTrue(ChatCredential.objects.filter(group=group,user=self.peer).exists())
