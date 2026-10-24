"""Opt-in ordinary API integration with actual Frienden in an isolated process."""
import io
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from django.test import TestCase,override_settings
from .models import User,ResearchGroup,Membership,ChatCredential
from . import frienden_api as api

@unittest.skipUnless(os.environ.get('ABANDON_PROVIDER_FIXTURE')=='1','opt-in actual provider fixture')
class ProviderIntegrationTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        config=override_settings(CHAT_VAULT_DIR=Path(self.tmp.name)/'vault');config.enable();self.addCleanup(config.disable)
        self.log=open(Path(self.tmp.name)/'provider.log','w+');self.addCleanup(self.log.close)
        script=Path(__file__).resolve().parents[2]/'scripts/provider_fixture_bridge.py'
        self.bridge=subprocess.Popen([os.environ.get('ABANDON_PROVIDER_PYTHON',os.sys.executable),str(script)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,cwd=self.tmp.name,env=os.environ)
        self.addCleanup(self.cleanup)
        line=self.bridge.stdout.readline()
        if not line:
            self.log.seek(0);self.fail(self.log.read()[-2500:])
        self.compact_responses=[];self.fixture_encoding='gzip'
        self.info=json.loads(line)
        self.assertTrue(self.info['ready'])
        mock=patch('core.frienden_api.build_opener');self.opener=mock.start();self.addCleanup(mock.stop)
        self.opener.return_value.open.side_effect=self.transport
        self.teacher=User.objects.create_user(username='local-mentor',email='local-mentor@example.test')
        self.student=User.objects.create_user(username='local-student',email='local-student@example.test')
        self.client.force_login(self.teacher)
    def cleanup(self):
        self.bridge.terminate()
        try:self.bridge.wait(timeout=5)
        except subprocess.TimeoutExpired:self.bridge.kill();self.bridge.wait()
        self.bridge.stdin.close();self.bridge.stdout.close()
    def rpc(self,data):
        self.bridge.stdin.write(json.dumps(data)+'\n');self.bridge.stdin.flush()
        line=self.bridge.stdout.readline();self.assertTrue(line,'provider exited')
        value=json.loads(line);self.assertNotIn('bridge_error',value);return value
    def transport(self,request,timeout):
        url=urlsplit(request.full_url);self.assertEqual(url.netloc,'api.sermo.jyonn.space');self.assertNotIn('oauth',url.path)
        value=self.rpc({'method':request.method,'path':url.path,'accept_encoding':self.fixture_encoding,'query':{k:v[0] for k,v in parse_qs(url.query).items()},'body':json.loads(request.data) if request.data else None,'token':request.get_header('Authorization','').removeprefix('Bearer ')})
        payload=value.get('data',{}).get('body')
        if isinstance(payload,dict) and payload.get('format')=='compact-v1': self.compact_responses.append(url.path)
        if value['status']>=400:raise HTTPError(request.full_url,value['status'],'fixture provider rejection',{},None)
        class Response(io.BytesIO):headers={}
        return Response(json.dumps(value['data']).encode())
    def post(self,path,body):return self.client.post(path,body,content_type='application/json')
    def create(self):
        result=self.post('/api/groups/',{'name':'Actual provider fixture','request_key':str(uuid.uuid4()),'space_slug':self.info['slug'],'account_name':self.info['official_name'],'password':'fixture-account-password'})
        self.assertEqual(result.status_code,201,result.content)
        return result.json()['group_id']
    def test_official_create_student_join_private_text_history_and_token_refresh(self):
        gid=self.create()
        invite=self.post(f'/api/groups/{gid}/invitations/',{})
        self.assertEqual(invite.status_code,201,invite.content)
        self.client.force_login(self.student)
        joined=self.post('/api/join/'+invite.json()['token']+'/',{'name':self.info['student_name'],'password':'fixture-student-password'})
        self.assertEqual(joined.status_code,200,joined.content)
        peers=self.client.get(f'/api/groups/{gid}/chat/conversations/').json()['peers']
        self.assertEqual(peers[0]['id'],str(self.teacher.pk))
        direct=self.post(f'/api/groups/{gid}/chat/conversations/',{'peer_id':str(self.teacher.pk)})
        self.assertEqual(direct.status_code,200,direct.content)
        cid=str(uuid.uuid4())
        sent=self.post(f'/api/groups/{gid}/chat/messages/',{'content':'隔离测试的研究讨论','client_message_id':cid,'room_key':direct.json()['key']})
        self.assertEqual(sent.status_code,200,sent.content)
        self.assertEqual(sent.json()['message']['sender_id'],str(self.info['student_id']))
        again=self.post(f'/api/groups/{gid}/chat/messages/',{'content':'隔离测试的研究讨论','client_message_id':cid,'room_key':direct.json()['key']})
        self.assertEqual(again.json()['message']['id'],sent.json()['message']['id'])
        history=self.client.get(f'/api/groups/{gid}/chat/messages/',{'room_key':direct.json()['key']})
        self.assertEqual(history.status_code,200,history.content)
        self.assertIn('隔离测试的研究讨论',[m['content'] for m in history.json()['messages']])
        # Exercise the provider's compact branch as well as its gzip-cost fallback.
        self.fixture_encoding='identity'
        self.rpc({'command':'history','chat_id':int(direct.json()['id'])})
        latest=self.client.get(f'/api/groups/{gid}/chat/messages/',{'room_key':direct.json()['key']})
        self.assertEqual(latest.status_code,200,latest.content)
        self.assertEqual(len(latest.json()['messages']),50);self.assertTrue(latest.json()['has_more'])
        self.assertIn('/messages/',self.compact_responses)
        older=self.client.get(f'/api/groups/{gid}/chat/messages/',{'room_key':direct.json()['key'],'before':latest.json()['next_before']})
        self.assertEqual(older.status_code,200,older.content);self.assertGreater(len(older.json()['messages']),0)
        from django.utils import timezone
        from datetime import timedelta
        ChatCredential.objects.filter(user=self.student).update(expires_at=timezone.now()-timedelta(seconds=1))
        status=self.client.get(f'/api/groups/{gid}/chat/status/')
        self.assertEqual(status.status_code,200,status.content);self.assertTrue(status.json()['connected'])
    def test_unknown_name_is_check_only_and_personal_account_cannot_be_mentor(self):
        before=self.rpc({'command':'counts'})['users']
        for name,password in [('typo-name','fixture-student-password'),(self.info['student_name'],'fixture-student-password')]:
            result=self.post('/api/groups/',{'name':'Rejected fixture','request_key':str(uuid.uuid4()),'space_slug':self.info['slug'],'account_name':name,'password':password})
            self.assertIn(result.status_code,(400,403),result.content)
            self.assertFalse(ResearchGroup.objects.exists())
        self.assertEqual(self.rpc({'command':'counts'})['users'],before)
