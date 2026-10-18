"""Session-authenticated group chat routes. Production feature stays disabled."""
import secrets
import time
import uuid
from functools import wraps
from django.db import transaction, IntegrityError
from django.db.models import F
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.debug import sensitive_variables
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from . import chat_service as svc
from .models import ChatCredential, GroupChatRoom, ResearchGroup, ChatAuthorizationAttempt
from .group_join import frontend, digest
from .views import membership, mentor


def guarded(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            response=function(*args, **kwargs)
            if isinstance(response.data,dict): response.data.update(viewer_id=str(args[0].user.pk),group_id=str(kwargs.get('group_id',args[1] if len(args)>1 else '')))
            return response
        except svc.ChatError as error:
            if error.code=='authorization_required' and hasattr(error,'credential_id'):
                ChatCredential.objects.filter(pk=error.credential_id,generation=error.credential_generation).update(ciphertext='',status='expired')
            return Response({'error':str(error),'code':error.code},status=error.status)
    return wrapped


def room_json(group_id,space_id):
    room=GroupChatRoom.objects.filter(group_id=group_id,space_id=space_id).first()
    return {'title':room.title,'key':str(room.generation)} if room else None


@api_view(['GET'])
@guarded
def status(request,group_id):
    member=membership(group_id,request.user)
    if not svc.enabled(): return Response({'enabled':False,'connected':False,'room':None,'can_configure':member.role=='mentor'})
    try:
        _,binding,_=svc.context(group_id,request.user)
        result={'enabled':True,'connected':False,'room':room_json(group_id,binding.space_id),'can_configure':member.role=='mentor'}
        try:
            credential,_=svc.credential_for(group_id,request.user)
            result.update(connected=True,expires_at=credential.expires_at,self_id=credential.subject.split(':')[2])
        except svc.ChatError as error:
            if error.code not in ('authorization_required','identity_required'): raise
        return Response(result)
    except svc.ChatError as error:
        if error.code!='identity_required': raise
        return Response({'enabled':True,'connected':False,'room':None,'can_configure':member.role=='mentor','identity_required':True})


@api_view(['POST','DELETE'])
@guarded
@sensitive_variables()
def connection(request,group_id):
    membership(group_id,request.user)
    if request.method=='POST':
        if set(request.data)!={'consent'} or request.data['consent'] is not True: raise svc.ChatError('invalid_request')
        return Response(svc.begin(request,group_id))
    if request.data: raise svc.ChatError('invalid_request')
    if not svc.enabled(): raise svc.ChatError('not_enabled',503)
    credential=ChatCredential.objects.filter(group_id=group_id,user=request.user).first();token=None
    if credential and credential.status=='active':
        try: _,token=svc.credential_for(group_id,request.user)
        except svc.ChatError: pass
    if credential: ChatCredential.objects.filter(pk=credential.pk,generation=credential.generation).update(ciphertext='',status='revoked')
    ChatAuthorizationAttempt.objects.filter(group_id=group_id,user=request.user,status='pending').update(status='cancelled')
    pending=request.session.get('chat_oauth_pending')
    if pending and pending.get('group_id')==group_id: request.session.pop('chat_oauth_pending',None)
    revoked=None
    if token:
        try: svc.transport('POST','/users/oauth/revoke',{},token);revoked=True
        except svc.ChatError: revoked=False
    return Response({'connected':False,'provider_revoked':revoked})


@api_view(['GET','POST'])
@guarded
@sensitive_variables()
def room(request,group_id):
    mentor(group_id,request.user)
    credential,token=svc.credential_for(group_id,request.user);rooms=svc.eligible_rooms(credential,token,request.user)
    if request.method=='GET':
        if request.GET: raise svc.ChatError('invalid_request')
        return Response({'rooms':rooms})
    if set(request.data)!={'chat_id'}: raise svc.ChatError('invalid_request')
    chosen=str(svc.integer(request.data['chat_id']));candidate=next((row for row in rooms if row['id']==chosen),None)
    if not candidate: raise svc.ChatError('access_denied',403)
    with transaction.atomic():
        ResearchGroup.objects.filter(pk=group_id).update(name=F('name'));svc.current(credential,request.user)
        value,_=GroupChatRoom.objects.update_or_create(group_id=group_id,defaults={'space_id':credential.space_id,
            'provider_chat_id':int(chosen),'title':candidate['title'],'generation':uuid.uuid4(),'configured_by':request.user})
    return Response({'room':{'title':value.title,'key':str(value.generation)}})


def query(request,kind):
    allowed={'room_key'}|({'before'} if kind=='messages' else {'after'})
    if set(request.GET)-allowed or any(len(request.GET.getlist(k))!=1 for k in request.GET): raise svc.ChatError('invalid_request')
    key=request.GET.get('room_key','')
    try:
        if str(uuid.UUID(key))!=key: raise ValueError
    except (ValueError,TypeError): raise svc.ChatError('invalid_request') from None
    return key,svc.integer(request.GET.get('before' if kind=='messages' else 'after',0),zero=True)


@api_view(['GET','POST'])
@guarded
@sensitive_variables()
def messages(request,group_id):
    credential,token=svc.credential_for(group_id,request.user)
    if request.method=='GET':
        key,cursor=query(request,'messages');selected=svc.selected(credential,token,request.user,key)
        data=svc.remote(credential,token,'GET',f'/users/oauth/chat/chats/{selected.provider_chat_id}/messages?'+svc.urlencode({'before':cursor,'limit':50}))
        rows=data.get('messages')
        if not isinstance(rows,list) or len(rows)>50 or type(data.get('has_more')) is not bool: raise svc.ChatError('invalid_response',502)
        allowed=svc.allowed_members(group_id,credential.space_id)
        result=[item for item in (svc.message(row) for row in rows) if item['sender_id'] in allowed]
        svc.current(credential,request.user,selected)
        return Response({'messages':result,'has_more':data['has_more'],
            'next_before':str(svc.integer(data['next_before'])) if data.get('next_before') is not None else None,'room_key':key})
    if request.GET or set(request.data)!={'content','client_message_id','room_key'}: raise svc.ChatError('invalid_request')
    text,key=request.data['content'],request.data['client_message_id']
    if not isinstance(text,str) or not text.strip() or len(text)>512 or not isinstance(key,str): raise svc.ChatError('invalid_request')
    try:
        if str(uuid.UUID(key))!=key: raise ValueError
    except ValueError: raise svc.ChatError('invalid_request') from None
    # Same lock order as exit/delete prevents a remote send after a completed exit.
    with transaction.atomic():
        ResearchGroup.objects.filter(pk=group_id).update(name=F('name'));svc.current(credential,request.user)
        selected=svc.selected(credential,token,request.user,request.data['room_key'])
        data=svc.remote(credential,token,'POST',f'/users/oauth/chat/chats/{selected.provider_chat_id}/messages',{'content':text,'client_message_id':key})
        item=svc.message(data.get('message'))
        if item['sender_id']!=credential.subject.split(':')[2] or item['client_message_id']!=key: raise svc.ChatError('invalid_response',502)
        svc.current(credential,request.user,selected)
        return Response({'message':item,'room_key':str(selected.generation)})


@api_view(['GET'])
@guarded
@sensitive_variables()
def events(request,group_id):
    credential,token=svc.credential_for(group_id,request.user);key,cursor=query(request,'events')
    selected=svc.selected(credential,token,request.user,key)
    data=svc.remote(credential,token,'GET',f'/users/oauth/chat/chats/{selected.provider_chat_id}/events?'+svc.urlencode({'after':cursor,'limit':100}))
    rows=data.get('events');next_after=svc.integer(data.get('next_after'),zero=True)
    if not isinstance(rows,list) or len(rows)>100 or type(data.get('has_more')) is not bool or next_after<cursor: raise svc.ChatError('invalid_response',502)
    result=[];last=cursor;allowed=svc.allowed_members(group_id,credential.space_id)
    for row in rows:
        if not isinstance(row,dict) or row.get('type') not in ('message.created','message.hidden','message.recalled','message.restored'): raise svc.ChatError('invalid_response',502)
        event_id=svc.integer(row.get('event_id'))
        if event_id<=last or event_id>next_after: raise svc.ChatError('invalid_response',502)
        last=event_id
        # Presence reflects current visibility even for old hidden/restored events.
        item=svc.message(row['message']) if row.get('message') else None;message_id=str(svc.integer(row.get('message_id')))
        if item and item['id']!=message_id: raise svc.ChatError('invalid_response',502)
        if item and item['sender_id'] not in allowed: item=None
        result.append({'id':str(event_id),'message_id':message_id,'message':item})
    if data['has_more'] and next_after==cursor: raise svc.ChatError('invalid_response',502)
    svc.current(credential,request.user,selected)
    return Response({'events':result,'next_after':str(next_after),'has_more':data['has_more'],'room_key':key})


@require_GET
@sensitive_variables()
def callback(request):
    pending=request.session.pop('chat_oauth_pending',None);target={'tab':'chat','chat_error':'failed'}
    if pending: target['group']=pending['group_id']
    try:
        generation=svc.complete(request,pending)
        request.session['chat_oauth_completed']={'group_id':pending['group_id'],'user_id':request.user.pk,'generation':str(generation),
            'state_hash':digest(request.GET['state']),'code_hash':digest(request.GET['code']),'expires':time.time()+300}
        target['chat']='connected';target.pop('chat_error')
    except (svc.ChatError,ValidationError,IntegrityError,NotFound,PermissionDenied,ValueError,KeyError,TypeError):
        if request.GET.get('error')=='access_denied': target['chat_error']='cancelled'
    return HttpResponseRedirect(frontend(target))


def completed_callback(request):
    receipt=request.session.get('chat_oauth_completed')
    if (not receipt or not request.user.is_authenticated or receipt.get('user_id')!=request.user.pk
        or receipt.get('expires',0)<time.time() or not svc.enabled() or request.get_host()!=svc.urlsplit(svc.settings.FRIENDOM_CALLBACK_URL).netloc
        or set(request.GET)!={'state','code'} or any(len(request.GET.getlist(k))!=1 for k in request.GET)
        or not secrets.compare_digest(digest(request.GET['state']),receipt['state_hash'])
        or not secrets.compare_digest(digest(request.GET['code']),receipt['code_hash'])): return None
    try: svc.context(receipt['group_id'],request.user)
    except (svc.ChatError,NotFound,PermissionDenied): return None
    if not ChatCredential.objects.filter(group_id=receipt['group_id'],user=request.user,generation=receipt['generation'],status='active',expires_at__gt=timezone.now()).exists(): return None
    return HttpResponseRedirect(frontend({'group':receipt['group_id'],'tab':'chat','chat':'connected'}))
