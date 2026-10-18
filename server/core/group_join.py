"""Group-scoped consent flow. No managed accounts or shared OTPs.
The new provider contract is disabled until explicitly configured and verified.
"""
import hashlib
import secrets
import time
import uuid
from datetime import timedelta, datetime, timezone as datetime_timezone
from urllib.parse import urlencode, urlsplit, parse_qs
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import F
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
                     InvitationAcceptance, Membership, User, FriendomIdentity, ResearchGroup, GroupActionReceipt, GroupCreationAttempt)
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


def create_group(request):
    """Start consent with a draft only. All three group rows are created on return."""
    name = request.data.get('name')
    try:
        key = uuid.UUID(str(request.data.get('request_key')))
    except (ValueError, TypeError, AttributeError):
        raise APIError('创建请求无效，请重新打开创建窗口')
    if set(request.data) != {'name', 'request_key'} or not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise APIError('请输入研究组名称')
    if not request.session.session_key:
        request.session.save()
    session_hash = digest(request.session.session_key)
    with transaction.atomic():
        attempt, _ = GroupCreationAttempt.objects.get_or_create(request_key=key, defaults={
            'user': request.user, 'session_hash': session_hash, 'name': name.strip(),
            'expires_at': timezone.now()+timedelta(minutes=30)})
        if attempt.user_id != request.user.pk or not secrets.compare_digest(attempt.session_hash, session_hash):
            raise PermissionDenied('创建请求不属于当前会话')
        if attempt.group_id:
            if not Membership.objects.filter(group_id=attempt.group_id, user=request.user).exists():
                raise NotFound('此前创建的研究组已不可访问，请重新创建')
            return Response({'group_id': attempt.group_id, 'completed': True})
        if attempt.name != name.strip():
            raise APIError('名称已变更，请重新开始创建')
        # Retrying a cancelled/expired draft starts a fresh short-lived proof.
        attempt.expires_at = timezone.now()+timedelta(minutes=30)
        attempt.save(update_fields=['expires_at'])
    return start_flow(request, {'purpose': 'space_admin', 'creation_key': str(key)})


@api_view(['GET', 'POST'])
def binding(request, group_id):
    membership(group_id, request.user)
    saved = GroupSpaceBinding.objects.filter(group_id=group_id).first()
    current = binding_for(group_id)
    if request.method == 'GET':
        info = binding_display(saved)
        return Response({'bound': bool(saved), 'verified': bool(current), **info,
                         'revision': saved.revision if saved else None,
                         'provider_ready': getattr(settings, 'FRIENDEN_SPACE_OAUTH_ENABLED', False)})
    mentor(group_id, request.user)
    # A binding is permanent. Renewal always uses the server's original space.
    if request.data:
        raise APIError('空间由既有绑定确定，不接受空间编号或换绑')
    context = {'purpose': 'space_admin', 'group_id': group_id, 'revision': saved.revision if saved else 0}
    if saved:
        context['space_id'] = saved.space_id
    return start_flow(request, context)


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


def public_space_metadata(space_id, slug):
    response = http_transport('GET', 'https://api.sermo.jyonn.space/spaces/lookup?' + urlencode({'slug': slug}), None, {})
    data = response.get('body') if isinstance(response, dict) else None
    if (not isinstance(data, dict) or type(data.get('space_id')) is not int or data['space_id'] != space_id
            or data.get('slug') != slug or not isinstance(data.get('name'), str) or not 1 <= len(data['name']) <= 200):
        raise FriendomError('Space metadata does not match binding')
    official = data.get('official_user')
    return {'slug': slug, 'name': data['name'], 'avatar_url': safe_space_avatar(official.get('avatar_uri')) if isinstance(official, dict) else ''}


def binding_display(binding, refresh=True):
    if not binding:
        return {'space_name': '', 'space_slug': '', 'space_avatar_url': ''}
    if (refresh and getattr(settings, 'FRIENDEN_SPACE_METADATA_ENABLED', False)
            and (not binding.metadata_at or binding.metadata_at < timezone.now()-timedelta(hours=6))):
        try:
            info = public_space_metadata(binding.space_id, binding.space_slug) if binding.space_slug else space_metadata(binding.space_id)
            binding.space_name, binding.space_slug = info['name'], info['slug']
            binding.space_avatar_url = info.get('avatar_url', '')
            binding.metadata_at = timezone.now()
            binding.save(update_fields=['space_name', 'space_slug', 'space_avatar_url', 'metadata_at'])
        except FriendomError:
            pass
    return {'space_name': binding.space_name, 'space_slug': binding.space_slug, 'space_avatar_url': safe_space_avatar(binding.space_avatar_url)}


def space_metadata(space_id):
    """Resolve public display metadata through a fixed provider endpoint, never a caller URL.
    Frozen requests expire unused; this neither authorizes a grant nor changes the binding.
    """
    target, _ = approved_adapter().begin(scopes=('profile', 'email'))
    params = {key: values[0] for key, values in parse_qs(urlsplit(target).query).items()}
    params.update(space_id=space_id, purpose='space_admin')
    response = http_transport('POST', 'https://api.sermo.jyonn.space/users/oauth/requests', params, {})
    rid = response.get('request_id', '') if isinstance(response, dict) else ''
    import re
    if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', rid):
        raise FriendomError('Invalid metadata request')
    metadata = http_transport('GET', 'https://api.sermo.jyonn.space/users/oauth/metadata?' + urlencode({'request_id': rid}), None, {})
    info = metadata.get('target_space') if isinstance(metadata, dict) else None
    if (not isinstance(info, dict) or type(info.get('id')) is not int or info.get('id') != space_id or not isinstance(info.get('slug'), str)
            or not info['slug'] or len(info['slug']) > 100 or not isinstance(info.get('name'), str)
            or not info['name'] or len(info['name']) > 200):
        raise FriendomError('Invalid space metadata')
    return public_space_metadata(space_id, info['slug'])


def start_flow(request, context):
    if not getattr(settings, 'FRIENDEN_SPACE_OAUTH_ENABLED', False):
        return Response({'error': '限定空间授权待 Frienden 友间本地契约核验；未发起真实授权。', 'code': 'provider_not_ready'}, status=503)
    if request.get_host() != urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc:
        return Response({'error': '请在当前服务来源发起授权'}, status=400)
    existing = request.session.get('group_oauth_pending')
    user_id = request.user.pk if request.user.is_authenticated else None
    if (existing and existing.get('expires', 0) > time.time() and existing.get('user_id') == user_id
            and all(existing.get(key) == value for key, value in context.items())):
        return Response({'authorize_url': 'https://frienden.jyonn.space/oauth/authorize?' + urlencode({'request_id': existing['request_id']}), 'resumed': True})
    started_at = time.time()
    adapter = approved_adapter()
    from urllib.parse import urlparse
    # Build the original seven OAuth fields server-side; never accept caller redirects.
    target, pending = adapter.begin(scopes=('profile', 'email'))
    params = {key: values[0] for key, values in parse_qs(urlparse(target).query).items()}
    if 'space_id' in context:
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
                    'request_id': rid, 'started_at': started_at})
    request.session.pop('chat_oauth_pending', None)
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
            raise APIError('先由导师验证 Frienden 友间空间管理员身份，再创建邀请')
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
        return Response({'group_name': invite.group.name, 'member_count': Membership.objects.filter(group_id=invite.group_id).count(), 'mentor_name': invite.created_by.first_name,
                         **binding_display(b), 'expires_at': invite.expires_at,
                         'provider_ready': getattr(settings, 'FRIENDEN_SPACE_OAUTH_ENABLED', False)})
    if request.data.get('consent') is not True or set(request.data) != {'consent'}:
        raise APIError('请确认通过指定 Frienden 友间空间验证身份并加入本组')
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


def completed_callback(request):
    """A repeated browser return can acknowledge completion, never exchange twice."""
    receipt = request.session.get('group_oauth_completed')
    if (not receipt or not request.user.is_authenticated
            or receipt.get('user_id') != request.user.pk or receipt.get('expires', 0) < time.time()
            or request.get_host() != urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc
            or set(request.GET) != {'state', 'code'}
            or any(len(request.GET.getlist(key)) != 1 for key in ('state', 'code'))
            or not secrets.compare_digest(receipt.get('state_hash', ''), digest(request.GET['state']))
            or not secrets.compare_digest(receipt.get('code_hash', ''), digest(request.GET['code']))
            or not Membership.objects.filter(group_id=receipt['group_id'], user=request.user).exists()):
        return None
    return HttpResponseRedirect(frontend({'group': receipt['group_id'], 'tab': receipt['tab']}))


@require_GET
@sensitive_variables('pending', 'profile')
def callback(request):
    pending = request.session.pop('group_oauth_pending', None)
    failure_query = {'join_error': 'verification_failed'}
    if pending and pending.get('creation_key'):
        failure_query = {'create_error': 'verification_failed'}
    elif pending and pending.get('purpose') == 'space_admin':
        failure_query.update({'group': pending['group_id'], 'tab': 'members'})
    if request.GET.get('error') == 'access_denied':
        failure_query['create_error' if pending and pending.get('creation_key') else 'join_error'] = 'cancelled'
    elif pending and pending.get('expires', 0) < time.time():
        failure_query['create_error' if pending and pending.get('creation_key') else 'join_error'] = 'expired'
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
        if not secrets.compare_digest(request.GET['state'], pending.get('state', '')):
            raise FriendomError('Wrong state')
        profile = approved_adapter().complete(code=request.GET['code'], state=request.GET['state'], pending=pending)
        space = profile.get('space_id') if 'space_id' not in pending else pending['space_id']
        if type(space) is not int or space < 1 or profile.get('space_id') != space or profile['subject'].split(':')[1] != str(space):
            raise FriendomError('Wrong space')
        with transaction.atomic():
            if pending.get('creation_key'):
                # A write lock serializes callbacks, including SQLite deployments.
                if not GroupCreationAttempt.objects.filter(pk=pending['creation_key']).update(name=F('name')):
                    raise FriendomError('Missing creation draft')
                attempt = GroupCreationAttempt.objects.select_for_update().get(pk=pending['creation_key'])
                if (attempt.user_id != request.user.pk or attempt.expires_at <= timezone.now()
                        or not secrets.compare_digest(attempt.session_hash, digest(request.session.session_key or ''))
                        or profile.get('space_admin') is not True):
                    raise FriendomError('Invalid creation proof')
                if attempt.group_id:
                    if not Membership.objects.filter(group_id=attempt.group_id, user=request.user).exists():
                        raise FriendomError('Created group no longer accessible')
                else:
                    resolve_user(profile, request.user, space)
                    group = ResearchGroup.objects.create(name=attempt.name, owner=request.user)
                    Membership.objects.create(group=group, user=request.user, role='mentor')
                    GroupSpaceBinding.objects.create(group=group, space_id=space, admin_subject=profile['subject'],
                        verified_at=timezone.now(), expires_at=timezone.now()+timedelta(hours=24))
                    attempt.group_id = group.pk
                    attempt.name = ''  # Completed receipt retains no research-group name.
                    attempt.save(update_fields=['group_id', 'name'])
                target = {'group': attempt.group_id, 'tab': 'members', 'created': '1'}
            else:
                if not ResearchGroup.objects.filter(pk=pending['group_id']).update(name=F('name')):
                    raise FriendomError('Group no longer exists')
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
                    begun = datetime.fromtimestamp(pending.get('started_at', 0), tz=datetime_timezone.utc)
                    if GroupActionReceipt.objects.filter(user=user, group_id=invite.group_id, action='leave', created_at__gt=begun).exists():
                        raise FriendomError('An earlier join cannot undo a later exit')
                    Membership.objects.get_or_create(group_id=invite.group_id, user=user, defaults={'role': 'student'})
                    InvitationAcceptance.objects.get_or_create(invitation=invite, user=user)
                    target = {'group': invite.group_id, 'tab': 'home'}
        if pending['purpose'] == 'join' and not request.user.is_authenticated:
            login_with_fixed_expiry(request, user, backend='django.contrib.auth.backends.ModelBackend')
        request.session['group_oauth_completed'] = {'user_id': request.user.pk if pending['purpose'] == 'space_admin' else user.pk, 'group_id': target['group'], 'tab': target['tab'], 'state_hash': digest(request.GET['state']), 'code_hash': digest(request.GET['code']), 'expires': time.time()+300}
        return HttpResponseRedirect(frontend(target))
    except (FriendomError, IntegrityError, ValidationError, APIError, NotFound, PermissionDenied, ValueError, KeyError):
        return fail()


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
