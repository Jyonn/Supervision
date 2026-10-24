"""Authenticated basic chat over Frienden's ordinary account API."""
import uuid
from functools import wraps
from django.db import transaction
from django.db.models import F
from django.views.decorators.debug import sensitive_variables
from rest_framework.decorators import api_view
from rest_framework.response import Response
from . import chat_service as svc
from . import frienden_api as provider
from .models import ChatCredential, ResearchGroup, Membership
from .views import membership, throttle


def guarded(function):
    @wraps(function)
    def wrapped(request,group_id):
        try:
            response=function(request,group_id)
            if isinstance(response.data,dict): response.data.update(viewer_id=str(request.user.pk),group_id=str(group_id))
            return response
        except provider.ProviderError as error:
            if getattr(error,'credential_id',None):
                ChatCredential.objects.filter(pk=error.credential_id,generation=error.credential_generation).update(ciphertext='',status='expired')
            return Response({'error':str(error),'code':error.code},status=error.status)
    return wrapped


@api_view(['GET'])
@guarded
def status(request,group_id):
    member=membership(group_id,request.user)
    _,binding,identity=svc.context(group_id,request.user)
    connected=False;name='';credential=None
    try:
        credential,token=svc.credential_for(group_id,request.user)
        verified=provider.verify(token,binding.space_id,member.role=='mentor')
        if f"sermo:{binding.space_id}:{verified['user_id']}"!=credential.subject: raise provider.ProviderError('identity_conflict',409)
        connected=True;name=verified['name']
    except provider.ProviderError as error:
        if credential and error.code in ('connection_required','identity_conflict','space_mismatch','official_required','student_required'):
            svc.invalidate(credential)
        if error.code not in ('connection_required',): raise
    return Response({'enabled':True,'connected':connected,'self_id':identity.subject.split(':')[2] if identity else '',
                     'name':name,'space_slug':binding.space_slug,'space_name':binding.space_name,'can_configure':member.role=='mentor'})


@api_view(['POST','DELETE'])
@guarded
@sensitive_variables()
def connection(request,group_id):
    member,binding,_=svc.context(group_id,request.user)
    if request.method=='DELETE':
        if request.data: raise provider.ProviderError('invalid_request',400)
        # A local disconnect never logs out the user's other Frienden clients.
        ChatCredential.objects.filter(group_id=group_id,user=request.user).update(ciphertext='',status='revoked',generation=uuid.uuid4())
        return Response({'connected':False})
    if set(request.data)!={'name','password'}: raise provider.ProviderError('invalid_request',400)
    if throttle(request,'frienden-account'): return Response({'error':'操作过于频繁，请稍后再试'},status=429)
    profile=provider.login(binding.space_slug,request.data['name'],request.data['password'],member.role=='mentor')
    if profile['space_id']!=binding.space_id: raise provider.ProviderError('space_mismatch',403)
    with transaction.atomic():
        ResearchGroup.objects.filter(pk=group_id).update(name=F('name'))
        svc.context(group_id,request.user)
        svc.store(request.user,ResearchGroup.objects.get(pk=group_id),profile)
    return Response({'connected':True})


@api_view(['GET','POST'])
@guarded
@sensitive_variables()
def conversations(request,group_id):
    credential,token=svc.credential_for(group_id,request.user)
    if request.method=='GET':
        allowed=svc.allowed_members(group_id,credential.space_id)
        peers=[]
        from .models import SpaceIdentity
        for member in Membership.objects.filter(group_id=group_id).exclude(user=request.user).select_related('user'):
            identity=SpaceIdentity.objects.filter(user=member.user,space_id=credential.space_id).first()
            if identity and identity.subject.split(':')[2] in allowed: peers.append({'id':str(member.user_id),'name':member.user.first_name or '组员'})
        return Response({'rooms':svc.rooms(credential,token,request.user),'peers':peers})
    if set(request.data)!={'peer_id'}: raise provider.ProviderError('invalid_request',400)
    from .models import SpaceIdentity
    peer=Membership.objects.filter(group_id=group_id,user_id=svc.integer(request.data['peer_id'])).exclude(user=request.user).first()
    identity=SpaceIdentity.objects.filter(user_id=peer.user_id,space_id=credential.space_id).first() if peer else None
    if not identity: raise provider.ProviderError('access_denied',403)
    with transaction.atomic():
        ResearchGroup.objects.filter(pk=group_id).update(name=F('name'));svc.current(credential,request.user)
        data=svc.remote(credential,token,'POST','/chats/direct',{'peer_user_id':svc.integer(identity.subject.split(':')[2])})
        key=svc.room_key(credential,svc.integer(data.get('chat_id')))
        svc.selected(credential,token,request.user,key)
    return Response({'key':key,'id':str(svc.integer(data.get('chat_id')))})


@api_view(['GET','POST'])
@guarded
@sensitive_variables()
def messages(request,group_id):
    credential,token=svc.credential_for(group_id,request.user)
    if request.method=='GET':
        if set(request.GET)-{'room_key','before'} or any(len(request.GET.getlist(k))!=1 for k in request.GET): raise provider.ProviderError('invalid_request',400)
        key=request.GET.get('room_key','');cid=svc.selected(credential,token,request.user,key)
        query={'chat_id':cid,'limit':50,'format':'compact-v1'}
        if 'before' in request.GET: query['before']=svc.integer(request.GET['before'])
        raw=svc.remote(credential,token,'GET','/messages/',query=query)
        if not isinstance(raw,list) or len(raw)>50: raise provider.ProviderError('invalid_response')
        allowed=svc.allowed_members(group_id,credential.space_id)
        items=[svc.message(row) for row in raw]
        items=[row for row in items if row['sender_id'] in allowed]
        svc.current(credential,request.user)
        return Response({'messages':items,'has_more':len(raw)==50,'next_before':str(min(svc.integer(row['message_id']) for row in raw)) if raw else None,'room_key':key})
    if request.GET or set(request.data)!={'content','client_message_id','room_key'}: raise provider.ProviderError('invalid_request',400)
    text,key=request.data['content'],request.data['client_message_id']
    if not isinstance(text,str) or not 1<=len(text.strip())<=512: raise provider.ProviderError('invalid_request',400)
    try:
        if not isinstance(key,str) or str(uuid.UUID(key))!=key: raise ValueError
    except (ValueError,TypeError): raise provider.ProviderError('invalid_request',400) from None
    with transaction.atomic():
        ResearchGroup.objects.filter(pk=group_id).update(name=F('name'));svc.current(credential,request.user)
        cid=svc.selected(credential,token,request.user,request.data['room_key'])
        data=svc.remote(credential,token,'POST','/messages/',{'content':text.strip(),'type':0,'client_message_id':key},query={'chat_id':cid})
        item=svc.message(data)
        if item['sender_id']!=credential.subject.split(':')[2] or item['client_message_id']!=key: raise provider.ProviderError('invalid_response')
        svc.current(credential,request.user)
    return Response({'message':item,'room_key':request.data['room_key']})
