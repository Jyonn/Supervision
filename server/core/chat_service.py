"""Local-only fixed-space delegated chat BFF. Never hands provider tokens to a browser."""
import base64
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from .models import (ResearchGroup, Membership, GroupSpaceBinding, SpaceIdentity,
                     ChatCredential, GroupChatRoom, ChatAuthorizationAttempt)
from .views import membership, mentor, valid_email
from .group_join import digest, frontend

SCOPES = ('profile', 'email', 'chat.read', 'chat.write')
ORIGIN = 'https://api.sermo.jyonn.space'
MESSAGES = {
    'not_enabled': '聊天接入尚未开通；未发起真实授权。',
    'identity_required': '请先使用本研究组绑定空间的同一身份完成加入或管理员验证。',
    'authorization_required': '聊天授权已失效，请由本人重新连接。',
    'provider_unavailable': 'Frienden 友间暂时无法连接，请稍后重试。',
    'send_uncertain': '发送结果尚未确认。请先同步消息，或用同一发送请求手动重试。',
    'access_denied': '当前会话成员或空间权限已变化，请重新检查。',
    'room_required': '请由导师选择本研究组的既有群会话。',
    'room_changed': '本组会话已变更，请重新加载。',
    'invalid_response': '聊天服务返回异常，已停止同步。',
    'invalid_request': '聊天请求无效。',
    'vault_unavailable': '聊天凭据存储暂不可用。',
}
class ChatError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(MESSAGES.get(code, MESSAGES['invalid_request']))

def enabled():
    return bool(getattr(settings, 'FRIENDEN_CHAT_ENABLED', False) and settings.FRIENDEN_SPACE_OAUTH_ENABLED
                and settings.FRIENDOM_CLIENT_ID == 'supervision-web')

def context(group_id, user):
    member = membership(group_id, user)
    if not enabled(): raise ChatError('not_enabled', 503)
    binding = GroupSpaceBinding.objects.filter(group_id=group_id, active=True).first()
    identity = SpaceIdentity.objects.filter(user=user, space_id=binding.space_id).first() if binding else None
    if not binding or not identity or not re.fullmatch(rf'sermo:{binding.space_id}:[1-9][0-9]*', identity.subject):
        raise ChatError('identity_required', 409)
    return member, binding, identity

def integer(value, zero=False):
    if type(value) not in (int, str) or not re.fullmatch(r'[0-9]{1,19}', str(value)):
        raise ChatError('invalid_request')
    value = int(value)
    if value < (0 if zero else 1) or value > 2**63-1: raise ChatError('invalid_request')
    return value

@sensitive_variables()
def transport(method, path, body=None, token=None):
    # URLs/headers are constructed here, never accepted from a browser.
    parsed = urlsplit(path); query = parse_qs(parsed.query)
    fixed = (method, parsed.path) in {('POST','/users/oauth/requests'),('POST','/users/oauth/token'),
        ('GET','/users/oauth/userinfo'),('POST','/users/oauth/revoke'),('GET','/users/oauth/chat/chats')}
    route = re.fullmatch(r'/users/oauth/chat/chats/[1-9][0-9]*/(messages|events)', parsed.path)
    allowed = fixed and not parsed.query
    if route:
        kind = route.group(1)
        keys = {'before','limit'} if kind == 'messages' else {'after','limit'}
        allowed = (method == 'GET' and set(query) <= keys and
            all(len(v)==1 and re.fullmatch(r'[0-9]{1,19}',v[0]) for v in query.values())) or (
            method == 'POST' and kind == 'messages' and not parsed.query)
    if parsed.scheme or parsed.netloc or parsed.fragment or not allowed: raise ChatError('invalid_request')
    headers = {'Content-Type':'application/json','Accept':'application/json'}
    if token: headers.update(Authorization='Bearer '+token, **{'X-OAuth-Client-ID':settings.FRIENDOM_CLIENT_ID})
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    request = Request(ORIGIN+path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with build_opener(NoRedirect).open(request, timeout=6) as response:
            raw=response.read(1048577)
            if len(raw)>1048576: raise ChatError('invalid_response',502)
            data=json.loads(raw)
            if not isinstance(data,dict): raise ChatError('invalid_response',502)
            return data
    except HTTPError as error:
        if error.code in (401,403): raise ChatError('authorization_required',401) from None
        raise ChatError('access_denied' if error.code==400 else 'provider_unavailable', error.code if error.code in (400,429) else 502) from None
    except (OSError, ValueError): raise ChatError('send_uncertain' if method=='POST' and path.endswith('/messages') else 'provider_unavailable',502) from None

@sensitive_variables()
def cipher(create=False):
    directory=Path(settings.CHAT_VAULT_DIR)
    try:
        if create: directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700: raise ValueError
        path=directory/'key'
        if create and not path.exists():
            try:
                fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'wb') as target: target.write(Fernet.generate_key())
            except FileExistsError: pass
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600: raise ValueError
        return Fernet(path.read_bytes())
    except (OSError,ValueError): raise ChatError('vault_unavailable',503) from None

def invalidate(credential):
    ChatCredential.objects.filter(pk=credential.pk,generation=credential.generation).update(ciphertext='',status='expired')

@sensitive_variables()
def credential_for(group_id, user):
    member,binding,identity=context(group_id,user)
    credential=ChatCredential.objects.filter(group_id=group_id,user=user).first()
    if not credential: raise ChatError('authorization_required',401)
    if (credential.status!='active' or credential.expires_at<=timezone.now()
            or credential.space_id!=binding.space_id or credential.subject!=identity.subject):
        invalidate(credential); raise ChatError('authorization_required',401)
    try:
        data=json.loads(cipher().decrypt(credential.ciphertext.encode()))
        expected={'user':user.pk,'group':group_id,'space':binding.space_id,'subject':identity.subject,'generation':str(credential.generation)}
        if any(data.get(k)!=v for k,v in expected.items()) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',data['token']): raise ValueError
    except (InvalidToken,ValueError,KeyError,TypeError,AttributeError):
        invalidate(credential); raise ChatError('authorization_required',401) from None
    return credential,data['token']

@sensitive_variables()
def remote(credential, token, method, path, body=None):
    try: return transport(method,path,body,token)
    except ChatError as error:
        if error.code=='authorization_required':
            error.credential_id=credential.pk;error.credential_generation=credential.generation
            invalidate(credential)
        raise

def current(credential, user, room=None):
    _,binding,identity=context(credential.group_id,user)
    if (binding.space_id!=credential.space_id or identity.subject!=credential.subject or
        not ChatCredential.objects.filter(pk=credential.pk,generation=credential.generation,status='active',expires_at__gt=timezone.now()).exists()):
        raise ChatError('authorization_required',401)
    if room and not GroupChatRoom.objects.filter(pk=room.pk,updated_at=room.updated_at,space_id=binding.space_id).exists():
        raise ChatError('room_changed',409)

def allowed_members(group_id, space_id):
    links=SpaceIdentity.objects.filter(space_id=space_id,user_id__in=Membership.objects.filter(group_id=group_id).values('user_id'))
    return {link.subject.split(':')[2] for link in links if re.fullmatch(rf'sermo:{space_id}:[1-9][0-9]*',link.subject)}

@sensitive_variables()
def eligible_rooms(credential, token, user):
    data=remote(credential,token,'GET','/users/oauth/chat/chats')
    if type(data.get('space_id')) is not int or data.get('space_id')!=credential.space_id or not isinstance(data.get('chats'),list) or len(data['chats'])>1000:
        raise ChatError('invalid_response',502)
    allowed=allowed_members(credential.group_id,credential.space_id); own=credential.subject.split(':')[2]; result=[]
    for row in data['chats']:
        if not isinstance(row,dict) or row.get('group') is not True or row.get('is_space_group') is not False: continue
        members=row.get('members')
        if not isinstance(members,list) or not members or len(members)>1000: continue
        roster=[]
        for item in members:
            if not isinstance(item,dict) or type(item.get('user_id')) is not int or not isinstance(item.get('name'),str): break
            roster.append({'id':str(integer(item['user_id'])),'name':item['name'][:80]})
        else:
            ids={item['id'] for item in roster}
            if own in ids and ids<=allowed:
                result.append({'id':str(integer(row.get('chat_id'))),'title':str(row.get('title') or '研究讨论')[:160],'members':roster})
    current(credential,user)
    return result

@sensitive_variables()
def selected(credential, token, user, room_key):
    room=GroupChatRoom.objects.filter(group_id=credential.group_id,space_id=credential.space_id).first()
    if not room: raise ChatError('room_required',409)
    if str(room.generation)!=room_key: raise ChatError('room_changed',409)
    if not any(item['id']==str(room.provider_chat_id) for item in eligible_rooms(credential,token,user)):
        raise ChatError('access_denied',403)
    return room

def message(data):
    if not isinstance(data,dict) or not isinstance(data.get('user'),dict): raise ChatError('invalid_response',502)
    sender=data['user']; content=data.get('content'); timestamp=data.get('created_at')
    if (not isinstance(sender.get('name'),str) or type(data.get('type')) is not int or
        type(timestamp) not in (int,float) or not math.isfinite(timestamp) or timestamp<0): raise ChatError('invalid_response',502)
    is_text=data['type']==0
    if is_text and (not isinstance(content,str) or len(content)>512): raise ChatError('invalid_response',502)
    return {'id':str(integer(data.get('message_id'))),'sender_id':str(integer(sender.get('user_id'))),
            'name':sender['name'][:80], 'content':content if is_text else None,
            'unsupported':not is_text,'created_at':timestamp,
            'client_message_id':data.get('client_message_id') if isinstance(data.get('client_message_id'),str) and len(data['client_message_id'])<=64 else ''}

@sensitive_variables()
def begin(request, group_id):
    _,binding,identity=context(group_id,request.user)
    if request.get_host()!=urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc: raise ChatError('invalid_request')
    existing=request.session.get('chat_oauth_pending')
    if existing and existing.get('group_id')==group_id and existing.get('user_id')==request.user.pk and existing.get('expires',0)>time.time():
        return {'authorize_url':existing['authorize_url']}
    verifier=secrets.token_urlsafe(48); state=secrets.token_urlsafe(32)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    data={'client_id':settings.FRIENDOM_CLIENT_ID,'redirect_uri':settings.FRIENDOM_CALLBACK_URL,'scope':' '.join(SCOPES),
          'state':state,'code_challenge':challenge,'code_challenge_method':'S256','response_type':'code',
          'space_id':binding.space_id,'purpose':'join'}
    response=transport('POST','/users/oauth/requests',data)
    rid=response.get('request_id'); ttl=response.get('expires_in')
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',rid) or type(ttl) is not int or not 1<=ttl<=1800: raise ChatError('invalid_response',502)
    url='https://frienden.jyonn.space/oauth/authorize?'+urlencode({'request_id':rid})
    if response.get('authorization_url')!=url: raise ChatError('invalid_response',502)
    if not request.session.session_key: request.session.save()
    attempt=ChatAuthorizationAttempt.objects.create(user=request.user,group_id=group_id,session_hash=digest(request.session.session_key),expires_at=timezone.now()+timedelta(seconds=ttl))
    request.session.pop('group_oauth_pending',None);request.session.pop('friendom_pending',None)
    request.session['chat_oauth_pending']={'group_id':group_id,'user_id':request.user.pk,'space_id':binding.space_id,'subject':identity.subject,
        'attempt':str(attempt.pk),'state':state,'verifier':verifier,'expires':time.time()+ttl,'authorize_url':url}
    return {'authorize_url':url}

@sensitive_variables()
def complete(request, pending):
    if (not pending or not enabled() or not request.user.is_authenticated or pending['user_id']!=request.user.pk
            or request.get_host()!=urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc or pending['expires']<=time.time()
            or request.GET.get('error') or set(request.GET)!={'code','state'}
            or any(len(request.GET.getlist(k))!=1 for k in ('code','state'))
            or not secrets.compare_digest(request.GET['state'],pending['state'])
            or not re.fullmatch(r'[A-Za-z0-9_-]{43}',request.GET['code'])): raise ChatError('authorization_required',401)
    _,binding,identity=context(pending['group_id'],request.user)
    if binding.space_id!=pending['space_id'] or identity.subject!=pending['subject']: raise ChatError('identity_required',409)
    attempt=ChatAuthorizationAttempt.objects.filter(pk=pending['attempt'],user=request.user,group_id=binding.group_id,
        session_hash=digest(request.session.session_key or ''),status='pending',expires_at__gt=timezone.now())
    if not attempt.update(status='claiming'): raise ChatError('authorization_required',401)
    token=None
    try:
        data=transport('POST','/users/oauth/token',{'grant_type':'authorization_code','client_id':settings.FRIENDOM_CLIENT_ID,
             'redirect_uri':settings.FRIENDOM_CALLBACK_URL,'code':request.GET['code'],'code_verifier':pending['verifier']})
        token=data.get('access_token'); ttl=data.get('expires_in')
        if not isinstance(data.get('scope'),str) or not isinstance(data.get('token_type'),str):
            token=None;raise ChatError('invalid_response',502)
        scopes=data['scope'].split()
        if (data.get('token_type','').lower()!='bearer' or not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token)
                or type(ttl) is not int or not 1<=ttl<=3600 or len(scopes)!=len(SCOPES) or set(scopes)!=set(SCOPES)):
            token=None; raise ChatError('invalid_response',502)
        profile=transport('GET','/users/oauth/userinfo',token=token)
        if (profile.get('sub')!=identity.subject or type(profile.get('space_id')) is not int or profile['space_id']!=binding.space_id
                or profile.get('email_verified') is not True or not isinstance(profile.get('email'),str)):
            raise ChatError('identity_required',409)
        valid_email(profile['email'])
        generation=uuid.uuid4()
        envelope={'token':token,'user':request.user.pk,'group':binding.group_id,'space':binding.space_id,
                  'subject':identity.subject,'generation':str(generation)}
        sealed=cipher(create=True).encrypt(json.dumps(envelope).encode()).decode()
        with transaction.atomic():
            ResearchGroup.objects.filter(pk=binding.group_id).update(name=F('name'))
            _,fresh,linked=context(binding.group_id,request.user)
            if fresh.space_id!=binding.space_id or linked.subject!=identity.subject: raise ChatError('identity_required',409)
            ChatCredential.objects.update_or_create(user=request.user,group_id=binding.group_id,defaults={
                'space_id':binding.space_id,'subject':identity.subject,'ciphertext':sealed,'expires_at':timezone.now()+timedelta(seconds=ttl),
                'generation':generation,'status':'active'})
            ChatAuthorizationAttempt.objects.filter(pk=pending['attempt'],status='claiming').update(status='complete')
        return generation
    except Exception:
        ChatAuthorizationAttempt.objects.filter(pk=pending['attempt'],status='claiming').update(status='failed')
        if token:
            try: transport('POST','/users/oauth/revoke',{},token)
            except ChatError: pass
        raise
