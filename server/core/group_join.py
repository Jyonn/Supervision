"""Research-group binding and invitations using ordinary Frienden accounts."""
from functools import wraps
import hashlib
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlencode, urlsplit
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.models import F
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_variables
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError as APIError
from .models import (ChatCredential, GroupSpaceBinding, GroupInvitation, SpaceIdentity, InvitationAcceptance,
                     Membership, ResearchGroup, GroupCreationAttempt)
from .views import mentor, membership, throttle
from . import frienden_api as provider
from . import chat_service as chat


def provider_errors(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        try: return fn(*args,**kwargs)
        except provider.ProviderError as error:
            if getattr(error,'credential_id',None):
                ChatCredential.objects.filter(pk=error.credential_id,generation=error.credential_generation).update(ciphertext='',status='expired')
            return Response({'error':str(error),'code':error.code},status=error.status)
    return wrapped


def digest(token): return hashlib.sha256(token.encode()).hexdigest()


def frontend(query):
    return (settings.FRONTEND_ORIGIN+'/' if settings.FRONTEND_ORIGIN else '/app/')+'?'+urlencode(query)


def binding_for(group_id):
    return GroupSpaceBinding.objects.filter(group_id=group_id,active=True).first()


def active_invitation(token):
    if not isinstance(token,str) or len(token)!=43: raise NotFound('邀请不存在或已失效')
    invite=GroupInvitation.objects.select_related('group','created_by').filter(token_hash=digest(token),revoked_at=None,expires_at__gt=timezone.now()).first()
    binding=binding_for(invite.group_id) if invite else None
    if not binding or binding.revision!=invite.binding_revision: raise NotFound('邀请不存在或空间绑定已变更')
    return invite,binding


def binding_display(binding,refresh=True):
    return {'space_name':binding.space_name if binding else '', 'space_slug':binding.space_slug if binding else '',
            'space_avatar_url':safe_space_avatar(binding.space_avatar_url) if binding else ''}


def bind_group(group,profile):
    space=profile['space'];official=space['official_user'];now=timezone.now()
    saved=GroupSpaceBinding.objects.filter(group=group).first()
    if saved and saved.space_id!=profile['space_id']: raise provider.ProviderError('space_mismatch',403)
    binding,_=GroupSpaceBinding.objects.update_or_create(group=group,defaults={
        'space_id':profile['space_id'],'space_slug':space['slug'],'space_name':space['name'],
        'space_avatar_url':safe_space_avatar(official.get('avatar_uri','')),'metadata_at':now,
        'admin_subject':f"sermo:{profile['space_id']}:{profile['user_id']}",
        'verified_at':now,'expires_at':now+timedelta(days=36500),'active':True})
    return binding


@sensitive_variables()
def create_group(request):
    if set(request.data)!={'name','request_key','space_slug','account_name','password'}: raise APIError('请输入研究组名称、空间和官方账户')
    name=request.data['name']
    if not isinstance(name,str) or not 1<=len(name.strip())<=100: raise APIError('请输入研究组名称')
    try: key=uuid.UUID(str(request.data['request_key']))
    except (ValueError,TypeError): raise APIError('创建请求无效') from None
    if not request.session.session_key: request.session.save()
    session_hash=digest(request.session.session_key)
    previous=GroupCreationAttempt.objects.filter(request_key=key).first()
    if previous:
        if previous.user_id!=request.user.pk or previous.session_hash!=session_hash: raise PermissionDenied('创建请求不属于当前会话')
        if previous.name!=name.strip(): raise APIError('研究组名称已变更，请重新创建')
        if previous.group_id:
            membership(previous.group_id,request.user)
            return Response({'group_id':previous.group_id,'completed':True})
    if throttle(request,'frienden-account'): return Response({'error':'操作过于频繁，请稍后再试'},status=429)
    try:
        profile=provider.login(request.data['space_slug'],request.data['account_name'],request.data['password'],True)
        with transaction.atomic():
            attempt,_=GroupCreationAttempt.objects.get_or_create(request_key=key,defaults={'user':request.user,'session_hash':session_hash,'name':name.strip(),'expires_at':timezone.now()+timedelta(minutes=30)})
            if attempt.user_id!=request.user.pk or attempt.session_hash!=session_hash: raise PermissionDenied('创建请求不属于当前会话')
            if attempt.group_id:
                membership(attempt.group_id,request.user)
                return Response({'group_id':attempt.group_id,'completed':True})
            group=ResearchGroup.objects.create(name=name.strip(),owner=request.user)
            Membership.objects.create(group=group,user=request.user,role='mentor')
            bind_group(group,profile);chat.store(request.user,group,profile)
            attempt.group_id=group.pk;attempt.save(update_fields=['group_id'])
        return Response({'completed':True,'group_id':group.pk},status=201)
    except provider.ProviderError as error: return Response({'error':str(error),'code':error.code},status=error.status)


@api_view(['GET','POST'])
@sensitive_variables()
def binding(request,group_id):
    membership(group_id,request.user)
    saved=GroupSpaceBinding.objects.filter(group_id=group_id).first()
    if request.method=='GET':
        return Response({'bound':bool(saved),'verified':bool(saved and saved.active),**binding_display(saved),'revision':saved.revision if saved else None,'provider_ready':True})
    mentor(group_id,request.user)
    fields={'name','password'} if saved else {'space_slug','name','password'}
    if set(request.data)!=fields: raise APIError('请输入空间官方账户的昵称和密码')
    if throttle(request,'frienden-account'): return Response({'error':'操作过于频繁，请稍后再试'},status=429)
    try:
        profile=provider.login(saved.space_slug if saved else request.data['space_slug'],request.data['name'],request.data['password'],True)
        with transaction.atomic():
            ResearchGroup.objects.filter(pk=group_id).update(name=F('name'));mentor(group_id,request.user)
            group=ResearchGroup.objects.get(pk=group_id)
            bind_group(group,profile);chat.store(request.user,group,profile)
        return Response({'bound':True,'verified':True,**binding_display(binding_for(group_id))})
    except provider.ProviderError as error: return Response({'error':str(error),'code':error.code},status=error.status)


@api_view(['GET','POST','DELETE'])
@provider_errors
def invitations(request,group_id):
    mentor(group_id,request.user)
    if request.method=='GET': return Response([{'id':str(i.id),'expires_at':i.expires_at,'revoked':bool(i.revoked_at)} for i in GroupInvitation.objects.filter(group_id=group_id).order_by('-created_at')])
    if request.method=='DELETE':
        try: key=uuid.UUID(str(request.data.get('id')))
        except (ValueError,TypeError): raise APIError('邀请编号无效') from None
        GroupInvitation.objects.filter(group_id=group_id,pk=key).update(revoked_at=timezone.now())
        return Response({'ok':True})
    with transaction.atomic():
        ResearchGroup.objects.filter(pk=group_id).update(name=F('name'));mentor(group_id,request.user)
        b=binding_for(group_id)
        if not b: raise APIError('请先连接空间官方账户')
        credential,token=chat.credential_for(group_id,request.user)
        verified=provider.verify(token,b.space_id,True)
        if f"sermo:{b.space_id}:{verified['user_id']}"!=credential.subject: raise APIError('官方账户身份已变化，请重新连接')
        raw=secrets.token_urlsafe(32)
        invite=GroupInvitation.objects.create(group_id=group_id,token_hash=digest(raw),binding_revision=b.revision,expires_at=timezone.now()+timedelta(days=7),created_by=request.user)
    url=frontend({'invite':raw})
    if url.startswith('/'): url=request.build_absolute_uri(url)
    return Response({'id':str(invite.pk),'token':raw,'join_url':url,'card_svg':invitation_card(invite,url),'expires_at':invite.expires_at},status=201)


@api_view(['GET','POST'])
@permission_classes([AllowAny])
@csrf_protect
@sensitive_variables()
def join(request,token):
    invite,b=active_invitation(token)
    if request.method=='GET': return Response({'group_name':invite.group.name,'member_count':Membership.objects.filter(group=invite.group).count(),'mentor_name':invite.created_by.first_name,**binding_display(b),'expires_at':invite.expires_at,'provider_ready':True})
    if not request.user.is_authenticated: raise PermissionDenied('请先登录 Supervision，再连接你在此空间的个人账户')
    if set(request.data)!={'name','password'}: raise APIError('请输入空间内个人账户的昵称和密码')
    if throttle(request,'frienden-account'): return Response({'error':'操作过于频繁，请稍后再试'},status=429)
    try:
        profile=provider.login(b.space_slug,request.data['name'],request.data['password'],False)
        if profile['space_id']!=b.space_id: raise provider.ProviderError('space_mismatch',403)
        with transaction.atomic():
            ResearchGroup.objects.filter(pk=invite.group_id).update(name=F('name'))
            invite,b=active_invitation(token)
            existing=Membership.objects.filter(group=invite.group,user=request.user).first()
            if existing and existing.role!='student': raise PermissionDenied('导师请在研究组内连接官方账户')
            chat.store(request.user,invite.group,profile)
            Membership.objects.get_or_create(group=invite.group,user=request.user,defaults={'role':'student'})
            InvitationAcceptance.objects.get_or_create(invitation=invite,user=request.user)
        return Response({'completed':True,'group_id':invite.group_id})
    except provider.ProviderError as error: return Response({'error':str(error),'code':error.code},status=error.status)


def safe_space_avatar(value):
    """The provider defines its space avatar as space.official_user.avatar_uri.
    This is NOT the authorizing person's avatar. Never proxy caller-supplied URLs.
    """
    if not isinstance(value, str) or len(value) > 2048:
        return ''
    parsed = urlsplit(value)
    if (parsed.scheme == 'https' and parsed.netloc in {'res.6-79.cn', 'api.sermo.jyonn.space', 'frienden.jyonn.space'}
            and not parsed.fragment and parsed.path.startswith(('/sermo/avatar/', '/assets/', '/avatars/'))):
        return value
    return ''


def space_avatar_data(binding):
    """Optional bounded raster from the provider-owned avatar host; no redirects."""
    if not binding or not getattr(settings, 'FRIENDEN_SPACE_METADATA_ENABLED', False):
        return ''
    url = safe_space_avatar(binding.space_avatar_url)
    if not url:
        return ''
    import urllib.request
    import base64
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=4) as response:
            raw = response.read(262145)
            if len(raw) > 262144:
                return ''
            kind = 'image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else 'image/webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else ''
            return 'data:'+kind+';base64,'+base64.b64encode(raw).decode() if kind else ''
    except (OSError, ValueError):
        return ''


def invitation_card(invite, url):
    import qrcode
    import unicodedata
    from pathlib import Path
    from zoneinfo import ZoneInfo
    from xml.sax.saxutils import escape
    def lines(text, width, limit):
        result, line, count = [], '', 0
        for char in text:
            size = 2 if unicodedata.east_asian_width(char) in ('W','F') else 1
            if count+size > width:
                result.append(line); line, count = '', 0
                if len(result) == limit:
                    result[-1] = result[-1][:-1]+'…'; return result
            line += char; count += size
        if line:result.append(line)
        return result
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(url); qr.make(fit=True); matrix = qr.get_matrix()
    cell = 300/len(matrix)
    squares = ''.join(f'<rect x="{120+x*cell:.4f}" y="{278+y*cell:.4f}" width="{cell:.4f}" height="{cell:.4f}"/>' for y,row in enumerate(matrix) for x,on in enumerate(row) if on)
    # Reuse the actual repository brand mark; the file is self-contained for canvas/PNG.
    logo = (Path(settings.BASE_DIR)/'brand'/'abandon.svg').read_text().split('>',1)[1].rsplit('</svg>',1)[0]
    binding = GroupSpaceBinding.objects.filter(group=invite.group).first()
    space = ((binding.space_name+' @'+binding.space_slug) if binding and binding.space_slug else '已关联研究空间')
    avatar = space_avatar_data(binding)
    avatar_svg = f'<image x="42" y="232" width="30" height="30" preserveAspectRatio="xMidYMid slice" href="{avatar}" clip-path="url(#space-avatar)"/>' if avatar else '<rect x="42" y="232" width="30" height="30" rx="8" fill="#dce4d8"/><path d="M50 240h5v5h-5zm9 0h5v5h-5zm-9 9h5v5h-5zm9 0h5v5h-5z" fill="#60745e"/>'
    title = ''.join(f'<text x="42" y="{176+i*36}" font-size="30" font-weight="600">{escape(line)}</text>' for i,line in enumerate(lines(invite.group.name,28,2)))
    expiry = invite.expires_at.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m/%d %H:%M')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="540" height="660" viewBox="0 0 540 660">
<defs><clipPath id="space-avatar"><rect x="42" y="232" width="30" height="30" rx="8"/></clipPath></defs>
<rect width="540" height="660" rx="24" fill="#f4ebdd"/>
<svg x="38" y="32" width="50" height="50" viewBox="0 0 64 64">{logo}</svg>
<g fill="#304737" font-family="PingFang SC,Microsoft YaHei,Noto Sans CJK SC,sans-serif">
<text x="102" y="65" font-size="24" font-weight="600">板凳<tspan dx="10" font-size="18" font-weight="400">Abandon</tspan></text>
<path d="M42 106h456" stroke="#d7cbb9"/><text x="42" y="140" font-size="14" fill="#68715f">研究组邀请</text>
{title}{avatar_svg}<text x="82" y="252" font-size="14">{escape(lines(space,48,1)[0])}</text>
<rect x="102" y="266" width="336" height="324" rx="16" fill="#fff"/><g shape-rendering="crispEdges">{squares}</g>
<text x="270" y="619" text-anchor="middle" font-size="20" font-weight="500">扫码加入研究组</text>
<text x="270" y="645" text-anchor="middle" font-size="16" fill="#68715f">有效至 {expiry}（北京时间）· Frienden 友间验证</text>
</g></svg>'''
