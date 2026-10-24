"""Private test-only bridge to ordinary Frienden API, using in-memory SQLite.

No deployment settings, external notifications, OAuth grants or real accounts.
"""
import contextlib
import json
import os
import sys
from urllib.parse import urlencode
sys.path.insert(0,os.environ['ABANDON_PROVIDER_SOURCE'])
os.environ['DJANGO_SETTINGS_MODULE']='Sermo.isolated_test_settings'
with contextlib.redirect_stdout(sys.stderr):
    import django
    django.setup()
    from django.conf import settings
    assert settings.DATABASES['default']['NAME']==':memory:'
    from django.test.utils import setup_databases
    setup_databases(verbosity=0,interactive=False)
    from django.test import Client
    from django.utils import timezone
    from unittest.mock import patch
    patch('User.models.NotificationEvent._enqueue_deliveries').start()
    from Space.models import Space
    from User.models import User
    from Chat.models import Chat
    from Message.models import Message
    from Friendship.models import Friendship, FriendshipStatusChoice
    space=Space.objects.create(name='Fictional research space',slug='fixture',email='fixture@example.test',chat_enabled=True,admin_phone_verified_at=timezone.now())
    official=space.ensure_official_user()
    official.set_password('fixture-account-password')
    student=User.create(space,'Fixture student',verified=True)
    student.set_password('fixture-student-password')
    for user in (official,student):
        User.objects.filter(pk=user.pk).update(is_permanent_vip=True,email=f'fixture-{user.pk}@example.test',email_verified_at=timezone.now(),phone=f'+8613800138{user.pk:03}',phone_verified_at=timezone.now())
    patch('Chat.views.check_user_text').start()
    patch('Message.views.check_user_text').start()
    patch('Message.views.NotificationEvent.emit_message_notifications').start()
    patch('User.models.User.log_login').start()
    patch('User.models.User.ensure_welcome_message').start()
    client=Client()
print(json.dumps({'ready':True,'space':space.pk,'slug':space.slug,'official_name':official.name,'student_name':student.name,'official_id':official.pk,'student_id':student.pk}),flush=True)
for line in sys.stdin:
    try:
        data=json.loads(line)
        if data.get('command')=='counts':
            result={'users':User.objects.count(),'messages':Message.objects.count()}
        elif data.get('command')=='history':
            chat=Chat.objects.get(pk=data['chat_id'])
            for index in range(80):
                Message.create(chat=chat,user=student,message_type=0,content='Fictional repeated research message '+str(index),client_message_id='fixture-history-'+str(index))
            result={'seeded':80}
        else:
            method=data['method'].lower();path=data['path']
            if data.get('query'):path+='?'+urlencode(data['query'])
            headers={'HTTP_ACCEPT_ENCODING':data.get('accept_encoding','gzip')}
            if data.get('token'):headers['HTTP_AUTHORIZATION']='Bearer '+data['token']
            with contextlib.redirect_stdout(sys.stderr):
                response=getattr(client,method)(path, data.get('body') or {},content_type='application/json',**headers) if method!='get' else client.get(path,**headers)
            result={'status':response.status_code,'data':response.json()}
        print(json.dumps(result),flush=True)
    except Exception as error:
        print(json.dumps({'bridge_error':type(error).__name__+':'+str(error)}),flush=True)
