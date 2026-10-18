"""Disposable loopback fixture for UI QA; never touches the real account DB."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
os.environ['DJANGO_SETTINGS_MODULE']='supervision.settings'
import django
from django.conf import settings
settings.DATABASES['default']['NAME']=Path(tempfile.mkdtemp(prefix='abandon-group-review-'))/'fixture.sqlite3'
settings.SESSION_COOKIE_NAME='abandon_group_fixture'
settings.CSRF_COOKIE_NAME='abandon_group_fixture_csrf'
settings.FRIENDOM_ENABLED=False
settings.CHRONELLE_AUTH_ENABLED=False
settings.CHRONELLE_ENABLED=False
settings.MIDDLEWARE.append('__main__.ReviewMiddleware')
class ReviewMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if request.path=='/__review__/':
            from core.session_auth import login_with_fixed_expiry
            from django.http import HttpResponseRedirect
            login_with_fixed_expiry(request,teacher,backend='django.contrib.auth.backends.ModelBackend')
            return HttpResponseRedirect('/app/?group='+str(group.pk)+'&tab=home')
        response=self.get_response(request)
        if request.path in ['/app/','/'] and response.status_code==200:
            from django.http import HttpResponse
            html=b''.join(response.streaming_content).decode() if response.streaming else response.content.decode()
            html=html.replace('</body>','<div style="position:fixed;bottom:0;right:0;padding:4px 10px;background:#354b3a;color:white;font-size:11px;z-index:9999">隔离验收 · 人物与研究数据均为虚构 · 外部授权关闭</div></body>')
            return HttpResponse(html)
        return response

django.setup()
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta
from core.models import User,ResearchGroup,Membership,ResearchTask,Entry,ResearchMeeting
call_command('migrate',verbosity=0)
teacher=User.objects.create(username='synthetic-mentor',email='mentor@example.test',first_name='林知远（虚构）',avatar_preset=4)
student=User.objects.create(username='synthetic-student',email='student@example.test',first_name='周遥（虚构）',avatar_preset=10)
group=ResearchGroup.objects.create(name='山海 · 交互研究组（虚构）',owner=teacher)
second=ResearchGroup.objects.create(name='留白 · 阅读小组（虚构）',owner=teacher)
for g in [group,second]:
    Membership.objects.create(group=g,user=teacher,role='mentor')
    Membership.objects.create(group=g,user=student,role='student')
for i,title in enumerate(['解释模型在跨域数据上的变化','整理访谈观察与研究问题','把本周阅读连成一份笔记']):
    t=ResearchTask.objects.create(group=group,creator=teacher,title=title,description='## 这次想弄清楚什么\n\n比较两种设置下的结果，记录过程与下一步。\n\n| 设置 | 观察 |\n| --- | --- |\n| A | 待补充 |')
    t.assignees.add(student,teacher)
    Entry.objects.create(task=t,owner=student,kind='report',day=timezone.now().date(),body='今天整理了第一轮观察。发现跨域差异比预期更集中，下一步检查样本分布。')
    if i==1:Entry.objects.create(task=t,owner=student,kind='summary',day=timezone.now().date(),body='已归纳三个后续问题，等待小组讨论。')
t=ResearchTask.objects.create(group=second,creator=teacher,title='只属于留白组的阅读记录');t.assignees.add(student)
m=ResearchMeeting.objects.create(group=group,organizer=teacher,title='研究进展短会',agenda='先说进展，再说卡住的地方。',starts_at=timezone.now()+timedelta(days=1),ends_at=timezone.now()+timedelta(days=1,hours=1));m.participants.add(teacher,student)
call_command('runserver','127.0.0.1:8003',use_reloader=False)
