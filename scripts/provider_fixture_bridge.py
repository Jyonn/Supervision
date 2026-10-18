"""Private stdin/stdout bridge to the actual provider with secret-free in-memory settings.
Only for the opt-in integration test. Never imports deployment settings or contacts production.
"""
import contextlib,json,os,sys
from urllib.parse import urlsplit,parse_qs,urlparse
provider=os.environ['ABANDON_PROVIDER_SOURCE']
sys.path.insert(0,provider);os.environ['DJANGO_SETTINGS_MODULE']='Sermo.isolated_test_settings'
with contextlib.redirect_stdout(sys.stderr):
 import django;django.setup()
 from django.conf import settings
 assert settings.DATABASES['default']['NAME']==':memory:', 'Fixture requires in-memory database'
 from django.test.utils import setup_databases
 from django.test import override_settings,Client
 from django.utils import timezone
 setup_databases(verbosity=0,interactive=False)
 override_settings(OAUTH_DELEGATED_ACTIONS_ENABLED=True).enable()
 from User.tests_oauth_collaboration import CollaborationTests
 fixture=CollaborationTests();fixture._pre_setup();fixture.setUp()
 from User.oauth_registry import approved
 from User.oauth_requests import resolve
 from User.oauth import authorize
 from User.models import User
 from unittest.mock import patch
 revision=approved('stillfolio');app=revision.application;app.client_id='supervision-web';app.space_admin_proof_enabled=True;app.save()
 revision.scopes=['profile','email','chat.read','chat.write','chat.modify','contacts.read','contacts.write','contacts.modify']
 revision.redirect_uris=['http://127.0.0.1:8001/api/auth/friendom/callback/'];revision.save()
 patch('User.oauth_chat.NotificationEvent.emit_message_notifications').start()
 client=Client()
 actors={'owner':fixture.user,'peer':fixture.peer,'admin':fixture.official}
 current='owner'
print(json.dumps({'ready':True,'space':fixture.space.id,'users':{k:v.pk for k,v in actors.items()}}),flush=True)
for line in sys.stdin:
 try:
  data=json.loads(line);command=data.get('command')
  if command=='actor':current=data['actor'];result={'ok':True}
  elif command=='consent':
   from utils.auth import get_login_token
   with contextlib.redirect_stdout(sys.stderr):
    response=client.post('/users/oauth/authorize',{'request_id':data['request_id']},content_type='application/json',HTTP_AUTHORIZATION='Bearer '+get_login_token(actors[current])['auth'])
   if response.status_code!=200: raise ValueError('Fixture consent rejected '+str(response.json()))
   result={'code':parse_qs(urlparse(response.json()['redirect_uri']).query)['code'][0]}
  else:
   method,path,body=data['method'],data['path'],data.get('body');headers={}
   if data.get('token'):headers={'HTTP_AUTHORIZATION':'Bearer '+data['token'],'HTTP_X_OAUTH_CLIENT_ID':'supervision-web'}
   with contextlib.redirect_stdout(sys.stderr):
    response=client.get(path,**headers) if method=='GET' else client.post(path,body or {},content_type='application/json',**headers)
   result={'status':response.status_code,'data':response.json()}
  print(json.dumps(result),flush=True)
 except Exception as error:
  print(json.dumps({'bridge_error':type(error).__name__}),flush=True)
