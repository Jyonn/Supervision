"""Group-scoped collaboration projection. No browser-selected space or identity."""
from types import SimpleNamespace
from django.core import signing
from django.db import transaction
from django.db.models import F
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.views.decorators.debug import sensitive_variables
from . import chat_service as svc
from .chat_views import guarded
from .models import ResearchGroup, GroupChatRoom, Membership, SpaceIdentity

ACTIONS={'invite','remove','transfer_owner','rename','background','mute','unmute','leave'}

def account(row):
    if not isinstance(row,dict) or not isinstance(row.get('name'),str): raise svc.ChatError('invalid_response',502)
    return {'id':str(svc.integer(row.get('user_id'))),'name':row['name'][:80]}

def key_for(credential,chat_id):
    return 'c:'+signing.dumps({'chat':str(chat_id),'group':credential.group_id,'user':credential.user_id,
        'generation':str(credential.generation)},salt='collaboration-room',compress=True)

def decode(credential,key):
    try:
        data=signing.loads(key[2:],salt='collaboration-room',max_age=3600)
        if {k:data.get(k) for k in ('group','user','generation')}!={'group':credential.group_id,'user':credential.user_id,'generation':str(credential.generation)}: raise ValueError
        return svc.integer(data['chat'])
    except (signing.BadSignature,ValueError,KeyError,TypeError,AttributeError): raise svc.ChatError('room_changed',409) from None

def detail(credential,token,user,chat_id):
    raw=svc.remote(credential,token,'GET',f'/users/oauth/chat/chats/{chat_id}').get('chat')
    result=project(credential,raw,user)
    if result['id']!=str(chat_id): raise svc.ChatError('invalid_response',502)
    return result

def project(credential,raw,user):
    if (not isinstance(raw,dict) or type(raw.get('group'))is not bool or raw.get('is_space_group') is not False
        or type(raw.get('is_owner'))is not bool or not isinstance(raw.get('members'),list) or len(raw['members'])>1000
        or not isinstance(raw.get('allowed_actions'),list) or any(not isinstance(a,str)or a not in ACTIONS for a in raw['allowed_actions'])
        or (raw.get('title')is not None and not isinstance(raw.get('title'),str))): raise svc.ChatError('invalid_response',502)
    roster=[]
    for row in raw['members']:
        if not isinstance(row,dict) or type(row.get('role'))is not int or row['role'] not in (0,1) or type(row.get('status'))is not int or row['status']!=1: raise svc.ChatError('invalid_response',502)
        roster.append({**account(row.get('user')),'role':row['role']})
    ids={r['id'] for r in roster};own=credential.subject.split(':')[2]
    if own not in ids or not ids<=svc.allowed_members(credential.group_id,credential.space_id): raise svc.ChatError('access_denied',403)
    if not raw['group'] and (len(ids)!=2 or raw['allowed_actions']): raise svc.ChatError('access_denied',403)
    svc.current(credential,user)
    cid=str(svc.integer(raw.get('chat_id')))
    title=raw.get('title')or('研究讨论'if raw['group']else '、'.join(r['name']for r in roster if r['id']!=own))
    return {'id':cid,'key':key_for(credential,cid),'title':title[:160],'group':raw['group'],
        'is_owner':raw['is_owner'],'members':roster,'allowed_actions':raw['allowed_actions'],
        'background_theme':raw.get('background_theme') if isinstance(raw.get('background_theme'),str) else ''}

def selected_conversation(credential,token,user,key):
    cid=decode(credential,key);value=detail(credential,token,user,cid)
    return SimpleNamespace(provider_chat_id=cid,generation=key,ephemeral=True,title=value['title'])

def peer(credential,value,allow_self=False):
    value=svc.integer(value);own=credential.subject.split(':')[2]
    if str(value) not in svc.allowed_members(credential.group_id,credential.space_id) or (str(value)==own and not allow_self): raise svc.ChatError('access_denied',403)
    return value

def ids(credential,values,empty=False):
    if not isinstance(values,list) or len(values)>100 or (not values and not empty): raise svc.ChatError('invalid_request')
    result=[peer(credential,v) for v in values]
    if len(result)!=len(set(result)): raise svc.ChatError('invalid_request')
    return result

def relation(credential,row):
    if not isinstance(row,dict) or type(row.get('outgoing'))is not bool or type(row.get('status'))is not int: raise svc.ChatError('invalid_response',502)
    who=account(row.get('peer'))
    return {'id':str(svc.integer(row.get('request_id'))),'peer':who,'status':row['status'],'outgoing':row['outgoing']}

def contacts(credential,token):
    data=svc.remote(credential,token,'GET','/users/oauth/contacts');allowed=svc.allowed_members(credential.group_id,credential.space_id)
    if not isinstance(data.get('friends'),list) or not isinstance(data.get('requests'),list) or len(data['friends'])>10000 or len(data['requests'])>10000: raise svc.ChatError('invalid_response',502)
    friends=[account(row.get('user')) for row in data['friends'] if isinstance(row,dict)]
    requests=[relation(credential,row) for row in data['requests']]
    return {'friends':[r for r in friends if r['id'] in allowed], 'requests':[r for r in requests if r['peer']['id'] in allowed]}

def dashboard(credential,token,user):
    data=contacts(credential,token);raw=svc.remote(credential,token,'GET','/users/oauth/chat/chats')
    if raw.get('space_id')!=credential.space_id or not isinstance(raw.get('chats'),list) or len(raw['chats'])>1000: raise svc.ChatError('invalid_response',502)
    rooms=[];allowed=svc.allowed_members(credential.group_id,credential.space_id)
    for r in raw['chats']:
        if not isinstance(r,dict) or r.get('is_space_group')is not False or type(r.get('group'))is not bool or not isinstance(r.get('members'),list): continue
        try: roster=[account(m) for m in r['members']]
        except svc.ChatError: continue
        members={m['id'] for m in roster}
        if credential.subject.split(':')[2] not in members or not members<=allowed: continue
        cid=str(svc.integer(r.get('chat_id')))
        rooms.append({'id':cid,'key':key_for(credential,cid),'title':str(r.get('title')or'讨论')[:160],'group':r['group'],'members':roster})
    cap=svc.remote(credential,token,'GET','/users/oauth/chat/capabilities')
    if type(cap.get('create_group'))is not bool: raise svc.ChatError('invalid_response',502)
    pending=svc.remote(credential,token,'GET','/users/oauth/chat/invitations').get('invitations')
    if not isinstance(pending,list) or len(pending)>100: raise svc.ChatError('invalid_response',502)
    fixed=GroupChatRoom.objects.filter(group_id=credential.group_id,space_id=credential.space_id).first()
    invitations=[]
    for row in pending:
        if not isinstance(row,dict): raise svc.ChatError('invalid_response',502)
        cid=svc.integer(row.get('chat_id'))
        if fixed and cid==fixed.provider_chat_id: invitations.append({'key':key_for(credential,cid),'title':str(row.get('title')or'研究组群')[:160]})
    own=credential.subject.split(':')[2]
    data.update(conversations=rooms,create_group=cap['create_group'] and Membership.objects.filter(group_id=credential.group_id,user=user,role='mentor').exists(),
        invitations=invitations,peers=[{'id':link.subject.split(':')[2],'name':link.user.first_name or '研究组成员'} for link in SpaceIdentity.objects.filter(space_id=credential.space_id,user_id__in=Membership.objects.filter(group_id=credential.group_id).values('user_id')).select_related('user') if link.subject.split(':')[2]!=own])
    svc.current(credential,user);return data

@api_view(['GET','POST'])
@guarded
@sensitive_variables()
def endpoint(request,group_id):
    credential,token=svc.credential_for(group_id,request.user)
    if request.method=='GET':
        if request.GET: raise svc.ChatError('invalid_request')
        return Response(dashboard(credential,token,request.user))
    if not isinstance(request.data,dict): raise svc.ChatError('invalid_request')
    body=dict(request.data);action=body.pop('action',None)
    if not isinstance(action,str): raise svc.ChatError('invalid_request')
    # Serialize local leave/deletion against every explicit provider mutation.
    with transaction.atomic():
        ResearchGroup.objects.filter(pk=group_id).update(name=F('name'));svc.current(credential,request.user)
        if action=='search':
            if set(body)!={'name'} or not isinstance(body['name'],str) or not 1<=len(body['name'].strip())<=20: raise svc.ChatError('invalid_request')
            value=svc.remote(credential,token,'GET','/users/oauth/contacts/search?'+svc.urlencode({'name':body['name'].strip()}))
            who=account(value['user']) if value.get('user') else None
            if who and who['id'] not in svc.allowed_members(group_id,credential.space_id): who=None
            result={'user':who} # never enumerate or expose unrelated members
        elif action=='friend_request':
            if set(body)!={'peer_user_id'}: raise svc.ChatError('invalid_request')
            value=svc.remote(credential,token,'POST','/users/oauth/contacts',{'peer_user_id':peer(credential,body['peer_user_id'])})
            result={'request':relation(credential,value.get('request'))}
        elif action=='friend_respond':
            if set(body)!={'request_id','accept'} or type(body['accept'])is not bool: raise svc.ChatError('invalid_request')
            rid=str(svc.integer(body['request_id']));rows=contacts(credential,token)['requests']
            if not any(r['id']==rid and not r['outgoing'] and r['status']==0 for r in rows): raise svc.ChatError('access_denied',403)
            value=svc.remote(credential,token,'POST',f'/users/oauth/contacts/requests/{rid}/respond',{'accept':body['accept']})
            result={'request':relation(credential,value.get('request'))}
        elif action=='direct':
            if set(body)!={'peer_user_id'}: raise svc.ChatError('invalid_request')
            target=peer(credential,body['peer_user_id'])
            if str(target) not in {r['id'] for r in contacts(credential,token)['friends']}: raise svc.ChatError('access_denied',403)
            value=svc.remote(credential,token,'POST','/users/oauth/chat/direct',{'peer_user_id':target})
            result={'conversation':project(credential,value.get('chat'),request.user)}
        elif action=='create_group':
            from .views import mentor
            mentor(group_id,request.user)
            if set(body)!={'user_ids','title'} or not isinstance(body['title'],str) or not 1<=len(body['title'].strip())<=50: raise svc.ChatError('invalid_request')
            members=ids(credential,body['user_ids'],empty=True)
            if svc.remote(credential,token,'GET','/users/oauth/chat/capabilities').get('create_group')is not True: raise svc.ChatError('access_denied',403)
            value=svc.remote(credential,token,'POST','/users/oauth/chat/groups',{'user_ids':members,'title':body['title'].strip()})
            result={'conversation':project(credential,value.get('chat'),request.user)}
        elif action=='invitation':
            if set(body)!={'key','accept'} or type(body['accept'])is not bool: raise svc.ChatError('invalid_request')
            cid=decode(credential,body['key']);fixed=GroupChatRoom.objects.filter(group_id=group_id,space_id=credential.space_id,provider_chat_id=cid).exists()
            if not fixed: raise svc.ChatError('access_denied',403)
            result=svc.remote(credential,token,'POST',f'/users/oauth/chat/chats/{cid}/invitation',{'accept':body['accept']})
            if result.get('chat_id')!=cid or result.get('accepted')is not body['accept']: raise svc.ChatError('invalid_response',502)
            result={'accepted':body['accept']}
        else:
            key=body.pop('key',None)
            if not isinstance(key,str): raise svc.ChatError('invalid_request')
            cid=decode(credential,key);chat=detail(credential,token,request.user,cid)
            if action=='select':
                if body: raise svc.ChatError('invalid_request')
                result={'conversation':chat}
            elif action in ('pinned','read'):
                if action=='pinned' and (set(body)!={'pinned'} or type(body['pinned'])is not bool) or action=='read' and body: raise svc.ChatError('invalid_request')
                svc.remote(credential,token,'POST',f'/users/oauth/chat/chats/{cid}/'+('preference' if action=='pinned' else 'read'),body)
                result={'ok':True}
            elif action in ACTIONS:
                expected={'invite':{'user_ids'},'remove':{'user_ids'},'transfer_owner':{'peer_user_id'},'rename':{'title'},'background':{'background_theme'},'mute':{'peer_user_id','duration'},'unmute':{'peer_user_id'},'leave':set()}
                if set(body)!=expected[action] or action not in chat['allowed_actions']: raise svc.ChatError('access_denied',403)
                if 'user_ids'in body: body['user_ids']=ids(credential,body['user_ids'])
                if 'peer_user_id'in body: body['peer_user_id']=peer(credential,body['peer_user_id'])
                current={r['id']:r for r in chat['members']}
                if action in ('remove','transfer_owner','mute','unmute'):
                    targets=body.get('user_ids',[body.get('peer_user_id')])
                    if any(str(v) not in current or (action in ('remove','mute') and current[str(v)]['role']==1) for v in targets): raise svc.ChatError('access_denied',403)
                if action=='rename' and (not isinstance(body['title'],str) or not 1<=len(body['title'].strip())<=50): raise svc.ChatError('invalid_request')
                if action=='background' and (not isinstance(body['background_theme'],str) or len(body['background_theme'])>32 or '/'in body['background_theme'] or ':'in body['background_theme'] or body['background_theme']=='custom'): raise svc.ChatError('invalid_request')
                if action=='mute' and body['duration'] not in ('10s','2m','10m','1h','6h','1d','7d'): raise svc.ChatError('invalid_request')
                value=svc.remote(credential,token,'POST',f'/users/oauth/chat/chats/{cid}/manage',{'action':action,**body})
                if action=='leave':
                    if value.get('chat_id')!=cid or value.get('left')is not True: raise svc.ChatError('invalid_response',502)
                    result={'left':True}
                else:
                    projected=project(credential,value.get('chat'),request.user)
                    if projected['id']!=str(cid): raise svc.ChatError('invalid_response',502)
                    result={'conversation':projected}
            else: raise svc.ChatError('invalid_request')
        if 'request'in result and result['request']['peer']['id'] not in svc.allowed_members(group_id,credential.space_id): raise svc.ChatError('invalid_response',502)
        svc.current(credential,request.user)
        return Response(result)
