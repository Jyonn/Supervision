"""Group-scoped ordinary Frienden account access, with encrypted rotating tokens."""
import json
import math
import re
import uuid
from datetime import timedelta
from cryptography.fernet import InvalidToken
from django.core import signing
from django.db import transaction, IntegrityError
from django.db.models import F
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from . import frienden_api as provider
from .frienden_api import ProviderError as ChatError, positive_id as integer
from .frienden_vault import cipher
from .models import ResearchGroup, Membership, GroupSpaceBinding, SpaceIdentity, ChatCredential


def enabled():
    return True


def context(group_id, user):
    from .views import membership
    member = membership(group_id, user)
    binding = GroupSpaceBinding.objects.filter(group_id=group_id, active=True).first()
    if not binding: raise ChatError('connection_required', 409)
    identity = SpaceIdentity.objects.filter(user=user, space_id=binding.space_id).first()
    return member, binding, identity


def invalidate(credential):
    ChatCredential.objects.filter(pk=credential.pk, generation=credential.generation).update(ciphertext='', status='expired')


@sensitive_variables()
def store(user, group, profile):
    subject = f"sermo:{profile['space_id']}:{profile['user_id']}"
    link = SpaceIdentity.objects.filter(user=user, space_id=profile['space_id']).first()
    if link and link.subject != subject: raise ChatError('identity_conflict', 409)
    try:
        linked, _ = SpaceIdentity.objects.get_or_create(subject=subject, defaults={'user':user, 'space_id':profile['space_id']})
        if linked.user_id != user.pk: raise ChatError('identity_conflict', 409)
    except IntegrityError:
        raise ChatError('identity_conflict', 409) from None
    generation = uuid.uuid4()
    data = {'version':2, 'group':group.pk, 'user':user.pk, 'space':profile['space_id'], 'subject':subject,
            'generation':str(generation), 'access':profile['access'], 'refresh':profile['refresh'], 'name':profile['name']}
    credential, _ = ChatCredential.objects.update_or_create(user=user, group=group, defaults={
        'space_id':profile['space_id'], 'subject':subject, 'generation':generation, 'status':'active',
        'ciphertext':cipher(create=True).encrypt(json.dumps(data).encode()).decode(),
        'expires_at':timezone.now()+timedelta(minutes=14)})
    return credential


@sensitive_variables()
def credential_for(group_id, user):
    member, binding, identity = context(group_id, user)
    credential = ChatCredential.objects.filter(user=user, group_id=group_id, status='active').first()
    if not identity or not credential or credential.subject != identity.subject or credential.space_id != binding.space_id:
        raise ChatError('connection_required', 409)
    try:
        data = json.loads(cipher().decrypt(credential.ciphertext.encode()))
        expected = {'version':2,'group':group_id,'user':user.pk,'space':binding.space_id,
                    'subject':identity.subject,'generation':str(credential.generation)}
        if any(data.get(k) != v for k,v in expected.items()): raise ValueError
        if not all(isinstance(data.get(k),str) and 16<=len(data[k])<=4096 for k in ('access','refresh')): raise ValueError
    except (InvalidToken,ValueError,TypeError,KeyError):
        invalidate(credential); raise ChatError('connection_required', 409) from None
    if credential.expires_at <= timezone.now():
        # Compare-and-swap keeps a late refresh from overwriting a reconnect.
        refresh_error = None
        with transaction.atomic():
            ResearchGroup.objects.filter(pk=group_id).update(name=F('name'))
            current(credential, user)
            fresh = ChatCredential.objects.get(pk=credential.pk)
            if fresh.ciphertext != credential.ciphertext:
                return credential_for(group_id, user)
            try:
                result = provider.call('POST','/users/refresh',{'refresh':data['refresh']})
                if not isinstance(result,dict) or not all(isinstance(result.get(k),str) and 16<=len(result[k])<=4096 for k in ('auth','refresh')):
                    raise ChatError('invalid_response')
                verified = provider.verify(result['auth'],binding.space_id,member.role=='mentor')
                if f"sermo:{binding.space_id}:{verified['user_id']}" != identity.subject: raise ChatError('identity_conflict',409)
                data.update(access=result['auth'],refresh=result['refresh'])
                credential.ciphertext=cipher().encrypt(json.dumps(data).encode()).decode()
                credential.expires_at=timezone.now()+timedelta(minutes=14)
                credential.save(update_fields=['ciphertext','expires_at'])
            except ChatError as error:
                refresh_error = error
        if refresh_error:
            if refresh_error.code in ('connection_required','access_denied','identity_conflict','space_mismatch','official_required','student_required'):
                invalidate(credential)
                refresh_error.credential_id=credential.pk
                refresh_error.credential_generation=credential.generation
            raise refresh_error
    return credential, data['access']


def current(credential, user):
    _,binding,identity=context(credential.group_id,user)
    if not identity or binding.space_id != credential.space_id or identity.subject != credential.subject or not ChatCredential.objects.filter(pk=credential.pk,generation=credential.generation,status='active').exists():
        raise ChatError('connection_required',409)


def remote(credential, token, method, path, body=None, query=None):
    try: return provider.call(method,path,body,token,query)
    except ChatError as error:
        if error.code=='connection_required':
            invalidate(credential)
            error.credential_id=credential.pk
            error.credential_generation=credential.generation
        raise


def allowed_members(group_id, space_id):
    links=SpaceIdentity.objects.filter(space_id=space_id,user_id__in=Membership.objects.filter(group_id=group_id).values('user_id'))
    return {link.subject.split(':')[2] for link in links if re.fullmatch(rf'sermo:{space_id}:[1-9][0-9]*',link.subject)}


def room_key(credential, chat_id):
    return signing.dumps({'chat':str(chat_id),'group':credential.group_id,'user':credential.user_id,'generation':str(credential.generation)},salt='frienden-basic-chat')


def rooms(credential, token, user):
    raw=remote(credential,token,'GET','/chats/',query={'format':'compact-v1'})
    if not isinstance(raw,list) or len(raw)>5000: raise ChatError('invalid_response')
    allowed=allowed_members(credential.group_id,credential.space_id)
    result=[]
    for row in raw:
        if not isinstance(row,dict) or not isinstance(row.get('members'),list): raise ChatError('invalid_response')
        members=row['members']; ids={str(integer(m.get('user_id'))) for m in members if isinstance(m,dict)}
        # All visible participants must belong to this research group. Space-wide
        # conversations and submissions are never silently projected here.
        if not ids or not ids<=allowed or credential.subject.split(':')[2] not in ids or row.get('is_space_group') or row.get('purpose') not in (None,'normal',0): continue
        cid=integer(row.get('chat_id'))
        title=row.get('title') or '、'.join(m.get('name','组员') for m in members if str(m['user_id'])!=credential.subject.split(':')[2]) or '会话'
        result.append({'key':room_key(credential,cid),'title':str(title)[:160],'id':str(cid),'group':bool(row.get('group')),
                       'unread':row.get('unread_count',0) if type(row.get('unread_count',0)) is int else 0})
    current(credential,user)
    return result


def selected(credential, token, user, key):
    if not isinstance(key,str) or len(key)>2000: raise ChatError('invalid_request',400)
    try:
        data=signing.loads(key,salt='frienden-basic-chat',max_age=86400)
        if {k:data.get(k) for k in ('group','user','generation')} != {'group':credential.group_id,'user':user.pk,'generation':str(credential.generation)}: raise ValueError
        cid=integer(data['chat'])
    except (signing.BadSignature,ValueError,TypeError,KeyError): raise ChatError('access_denied',403) from None
    if not any(row['id']==str(cid) for row in rooms(credential,token,user)): raise ChatError('access_denied',403)
    return cid


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
