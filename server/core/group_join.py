"""Group-scoped consent flow. No managed accounts or shared OTPs.
The new provider contract is disabled until explicitly configured and verified.
"""
import hashlib
import secrets
import time
import uuid
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, parse_qs
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import HttpResponseRedirect, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_variables
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError as APIError
from .models import (GroupSpaceBinding, GroupInvitation, SpaceIdentity,
                     InvitationAcceptance, Membership, User, FriendomIdentity)
from .views import mentor, membership, valid_email
from .friendom import approved_adapter, http_transport, FriendomError
from .session_auth import login_with_fixed_expiry


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def binding_for(group_id):
    return GroupSpaceBinding.objects.filter(group_id=group_id, active=True, expires_at__gt=timezone.now()).first()


def active_invitation(token):
    if not isinstance(token, str) or len(token) != 43:
        raise NotFound('邀请不存在或已失效')
    invite = GroupInvitation.objects.select_related('group', 'created_by').filter(token_hash=digest(token), revoked_at=None, expires_at__gt=timezone.now()).first()
    binding = binding_for(invite.group_id) if invite else None
    if not binding or invite.binding_revision != binding.revision:
        raise NotFound('邀请不存在、已撤销或空间绑定已变更')
    return invite, binding


def frontend(query):
    return (settings.FRONTEND_ORIGIN + '/' if settings.FRONTEND_ORIGIN else '/app/') + '?' + urlencode(query)


@api_view(['GET', 'POST'])
def binding(request, group_id):
    membership(group_id, request.user)
    current = binding_for(group_id)
    if request.method == 'GET':
        return Response({'bound': bool(current), 'space_id': current.space_id if current else None,
                         'revision': current.revision if current else None,
                         'provider_ready': getattr(settings, 'FRIENDEN_SPACE_OAUTH_ENABLED', False)})
    mentor(group_id, request.user)
    space = request.data.get('space_id')
    if type(space) is not int or space < 1 or set(request.data) != {'space_id'}:
        raise APIError('请输入 Frienden 空间编号')
    return start_flow(request, {'purpose': 'space_admin', 'group_id': group_id, 'space_id': space,
                                'revision': GroupSpaceBinding.objects.filter(group_id=group_id).values_list('revision', flat=True).first() or 0})


def start_flow(request, context):
    if not getattr(settings, 'FRIENDEN_SPACE_OAUTH_ENABLED', False):
        return Response({'error': '限定空间授权待 Frienden 本地契约核验；未发起真实授权。', 'code': 'provider_not_ready'}, status=503)
    if request.get_host() != urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc:
        return Response({'error': '请在当前服务来源发起授权'}, status=400)
    existing = request.session.get('group_oauth_pending')
    user_id = request.user.pk if request.user.is_authenticated else None
    if (existing and existing.get('expires', 0) > time.time() and existing.get('user_id') == user_id
            and all(existing.get(key) == value for key, value in context.items())):
        return Response({'authorize_url': 'https://frienden.jyonn.space/oauth/authorize?' + urlencode({'request_id': existing['request_id']}), 'resumed': True})
    adapter = approved_adapter()
    from urllib.parse import urlparse
    # Build the original seven OAuth fields server-side; never accept caller redirects.
    target, pending = adapter.begin(scopes=('profile', 'email'))
    params = {key: values[0] for key, values in parse_qs(urlparse(target).query).items()}
    params['space_id'] = context['space_id']
    params['purpose'] = context['purpose']
    try:
        result = http_transport('POST', 'https://api.sermo.jyonn.space/users/oauth/requests', params, {})
        url = urlsplit(result.get('authorization_url', ''))
        rid = result.get('request_id')
        if (url.scheme != 'https' or url.netloc != 'frienden.jyonn.space' or url.path != '/oauth/authorize'
                or url.fragment or not isinstance(rid, str) or not 16 <= len(rid) <= 256
                or parse_qs(url.query) != {'request_id': [rid]}):
            raise FriendomError('Invalid frozen authorization URL')
        ttl = result.get('expires_in')
        if type(ttl) is not int or not 1 <= ttl <= 1800:
            raise FriendomError('Invalid request expiry')
    except (FriendomError, AttributeError, TypeError):
        return Response({'error': '空间授权暂不可用，请稍后重试'}, status=503)
    pending.update(context)
    pending.update({'expires': time.time() + ttl, 'user_id': request.user.pk if request.user.is_authenticated else None,
                    'request_id': rid})
    request.session.pop('friendom_pending', None)
    request.session['group_oauth_pending'] = pending
    return Response({'authorize_url': result['authorization_url']})


@api_view(['GET', 'POST', 'DELETE'])
def invitations(request, group_id):
    mentor(group_id, request.user)
    if request.method == 'GET':
        return Response([{'id': str(i.id), 'expires_at': i.expires_at, 'revoked': bool(i.revoked_at)} for i in GroupInvitation.objects.filter(group_id=group_id).order_by('-created_at')])
    if request.method == 'DELETE':
        try:
            invitation_id = uuid.UUID(str(request.data.get('id')))
        except (ValueError, TypeError, AttributeError):
            raise APIError('邀请编号无效')
        GroupInvitation.objects.filter(group_id=group_id, pk=invitation_id).update(revoked_at=timezone.now())
        return Response({'ok': True})
    with transaction.atomic():
        b = GroupSpaceBinding.objects.select_for_update().filter(group_id=group_id, active=True, expires_at__gt=timezone.now()).first()
        if not b:
            raise APIError('先由导师验证 Frienden 空间管理员身份，再创建邀请')
        token = secrets.token_urlsafe(32)
        invite = GroupInvitation.objects.create(group_id=group_id, token_hash=digest(token), binding_revision=b.revision,
                                               expires_at=min(timezone.now()+timedelta(days=7), b.expires_at), created_by=request.user)
    # Raw token returned once; only its hash persists.
    url = frontend({'invite': token})
    if url.startswith('/'):
        url = request.build_absolute_uri(url)
    return Response({'id': str(invite.id), 'token': token, 'join_url': url, 'card_svg': invitation_card(invite, url), 'expires_at': invite.expires_at}, status=201)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
@csrf_protect
def join(request, token):
    invite, b = active_invitation(token)
    if request.method == 'GET':
        return Response({'group_name': invite.group.name, 'mentor_name': invite.created_by.first_name,
                         'space_id': b.space_id, 'expires_at': invite.expires_at,
                         'provider_ready': getattr(settings, 'FRIENDEN_SPACE_OAUTH_ENABLED', False)})
    if request.data.get('consent') is not True or set(request.data) != {'consent'}:
        raise APIError('请确认通过指定 Frienden 空间验证身份并加入本组')
    return start_flow(request, {'purpose': 'join', 'invite': token, 'group_id': invite.group_id,
                                'space_id': b.space_id, 'revision': b.revision})


def resolve_user(profile, current_user, space_id):
    """Only trusted userinfo reaches this function. Stable links outrank email."""
    subject = profile['subject']
    email = profile.get('verified_email')
    if not email:
        raise FriendomError('Verified email required')
    email = valid_email(email)
    link = SpaceIdentity.objects.select_related('user').filter(subject=subject).first()
    legacy = FriendomIdentity.objects.select_related('user').filter(subject=subject).first()
    if link and legacy and link.user_id != legacy.user_id:
        raise FriendomError('Conflicting stable identity records')
    mapped = link.user if link else legacy.user if legacy else None
    email_user = User.objects.filter(email__iexact=email).first()
    if mapped and email_user and mapped.pk != email_user.pk:
        raise FriendomError('Subject and email belong to different accounts')
    user = mapped or email_user
    if current_user.is_authenticated:
        if user and user.pk != current_user.pk:
            raise FriendomError('Current login differs from verified account')
        # Never attach a new email to the current account implicitly.
        if not user and (current_user.email or '').lower() != email:
            raise FriendomError('Current login differs from verified email')
        user = current_user
    if user is None:
        user = User(username=uuid.uuid4().hex, email=email, first_name=profile['name'], email_verified_at=timezone.now())
        user.set_unusable_password()
        user.save()
    if not user.is_active:
        raise FriendomError('Inactive account')
    same_space = SpaceIdentity.objects.filter(user=user, space_id=space_id).first()
    old = FriendomIdentity.objects.filter(user=user, subject__startswith=f'sermo:{space_id}:').first()
    if (same_space and same_space.subject != subject) or (old and old.subject != subject):
        raise FriendomError('Existing linked subject conflict')
    if user.email is None:
        user.email = email
        user.email_verified_at = timezone.now()
        user.save(update_fields=['email', 'email_verified_at'])
    SpaceIdentity.objects.get_or_create(user=user, space_id=space_id, defaults={'subject': subject})
    return user


@require_GET
@sensitive_variables('pending', 'profile')
def callback(request):
    pending = request.session.pop('group_oauth_pending', None)
    failure_query = {'join_error': 'verification_failed'}
    if pending and pending.get('purpose') == 'space_admin':
        failure_query.update({'group': pending['group_id'], 'tab': 'members'})
    if request.GET.get('error') == 'access_denied':
        failure_query['join_error'] = 'cancelled'
    elif pending and pending.get('expires', 0) < time.time():
        failure_query['join_error'] = 'expired'
    if pending and pending.get('invite'):
        failure_query['invite'] = pending['invite']
    def fail():
        return HttpResponseRedirect(frontend(failure_query))
    if (not pending or not getattr(settings, 'FRIENDEN_SPACE_OAUTH_ENABLED', False)
            or request.get_host() != urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc
            or pending.get('expires', 0) < time.time() or request.GET.get('error')
            or any(len(request.GET.getlist(k)) != 1 for k in ('state', 'code'))
            or (request.user.pk if request.user.is_authenticated else None) != pending.get('user_id')):
        return fail()
    try:
        profile = approved_adapter().complete(code=request.GET['code'], state=request.GET['state'], pending=pending)
        space = pending['space_id']
        if profile.get('space_id') != space or profile['subject'].split(':')[1] != str(space):
            raise FriendomError('Wrong space')
        with transaction.atomic():
            if pending['purpose'] == 'space_admin':
                mentor(pending['group_id'], request.user)
                if profile.get('space_admin') is not True:
                    raise FriendomError('Admin proof required')
                current = GroupSpaceBinding.objects.select_for_update().filter(group_id=pending['group_id']).first()
                if (current.revision if current else 0) != pending['revision']:
                    raise FriendomError('Binding changed during authorization')
                resolve_user(profile, request.user, space)
                GroupSpaceBinding.objects.update_or_create(group_id=pending['group_id'], defaults={
                    'space_id': space, 'admin_subject': profile['subject'], 'revision': pending['revision']+1,
                    'verified_at': timezone.now(), 'expires_at': timezone.now()+timedelta(hours=24), 'active': True})
                target = {'group': pending['group_id'], 'tab': 'members'}
            else:
                invite, binding = active_invitation(pending['invite'])
                # Lock the invitation and binding; re-check expiry/revocation inside transaction.
                GroupInvitation.objects.select_for_update().get(pk=invite.pk)
                GroupSpaceBinding.objects.select_for_update().get(group_id=invite.group_id)
                invite, binding = active_invitation(pending['invite'])
                if binding.revision != pending['revision'] or binding.space_id != space:
                    raise FriendomError('Invitation binding changed')
                user = resolve_user(profile, request.user, space)
                Membership.objects.get_or_create(group_id=invite.group_id, user=user, defaults={'role': 'student'})
                InvitationAcceptance.objects.get_or_create(invitation=invite, user=user)
                target = {'group': invite.group_id, 'tab': 'home'}
        if pending['purpose'] == 'join' and not request.user.is_authenticated:
            login_with_fixed_expiry(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return HttpResponseRedirect(frontend(target))
    except (FriendomError, IntegrityError, ValidationError, APIError, NotFound, PermissionDenied, ValueError, KeyError):
        return fail()


def invitation_card(invite, url):
    import qrcode
    from xml.sax.saxutils import escape
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    cell = 240 / len(matrix)
    squares = ''.join(f'<rect x="{150+x*cell:.4f}" y="{208+y*cell:.4f}" width="{cell:.4f}" height="{cell:.4f}"/>' for y,row in enumerate(matrix) for x,on in enumerate(row) if on)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="540" height="660" viewBox="0 0 540 660">
<rect width="540" height="660" rx="28" fill="#f5eee3"/><path d="M48 75h444" stroke="#dbd0c0"/>
<g fill="#354b3a" font-family="-apple-system,BlinkMacSystemFont,sans-serif"><text x="48" y="53" font-size="18">板凳 · Abandon</text>
<text x="48" y="126" font-size="26">一起把想法往前推。</text><text x="48" y="166" font-size="19">{escape(invite.group.name[:22])}</text>
<rect x="140" y="198" width="260" height="260" rx="12" fill="white"/>{squares}
<text x="270" y="501" text-anchor="middle" font-size="17">扫码加入研究组</text>
<text x="270" y="534" text-anchor="middle" font-size="13">在指定 Frienden 空间完成邮箱与手机认证</text>
<text x="270" y="558" text-anchor="middle" font-size="13">已有板凳账号按已验证邮箱复用</text>
<text x="270" y="602" text-anchor="middle" font-size="12">有效至 {invite.expires_at.strftime('%Y-%m-%d %H:%M UTC')} · 导师可随时撤销</text></g></svg>'''
