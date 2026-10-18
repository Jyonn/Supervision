import json
import uuid
import time
from urllib.parse import urlsplit
from datetime import date
from pathlib import Path
from django.conf import settings
from django.contrib.auth import authenticate, logout
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse, FileResponse, HttpResponseRedirect, Http404, HttpResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError as APIValidationError
from .models import User, ResearchGroup, Membership, ResearchTask, Entry, FriendomIdentity
from . import mail_verification
from .session_auth import login_with_fixed_expiry
from .user_profiles import avatar_profile, public_user_json
from django.utils import timezone


def user_json(user):
    identity = FriendomIdentity.objects.filter(user=user).first()
    avatar = avatar_profile(user, identity)
    return {'id': user.id, 'email': user.email, 'email_verified': bool(user.email_verified_at), 'name': user.first_name or user.email, 'avatar_source': user.avatar_source, **avatar, 'friendom_connected': bool(identity), 'friendom_gravatar_ready': bool(identity and identity.gravatar_email_hash), 'gravatar_email_source': user.gravatar_email_source}

@require_http_methods(['GET'])
def session(request):
    return JsonResponse({'user': user_json(request.user) if request.user.is_authenticated else None, 'csrf': get_token(request), 'email_verification': mail_verification.ready(), 'email_verification_required': settings.EMAIL_VERIFICATION_REQUIRED, 'password_recovery': False, 'external_identity': settings.FRIENDOM_ENABLED, 'friendom_email': settings.FRIENDOM_EMAIL_ENABLED, 'calendar_local': True, 'calendar_authorization': settings.CHRONELLE_AUTH_ENABLED, 'calendar_sync': settings.CHRONELLE_ENABLED})

@csrf_protect
@require_http_methods(['POST'])
def verification_send(request):
    try:
        data = payload(request)
        if set(data) != {'email', 'purpose'} or data.get('purpose') != 'registration':
            raise ValidationError('仅支持注册邮箱验证')
        email = valid_email(data.get('email'))
        if User.objects.filter(email=email).exists():
            return JsonResponse({'error': '该邮箱已注册，请直接登录；不会修改已有账号。'}, status=400)
        mail_verification.issue(request, email)
        return JsonResponse({'ok': True, 'expires_in': 600, 'retry_after': 60})
    except ValidationError:
        return JsonResponse({'error': '请求或邮箱格式无效'}, status=400)
    except mail_verification.MailError as error:
        return JsonResponse({'error': str(error)}, status=429 if '频繁' in str(error) else 503)


def payload(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeDecodeError):
        raise ValidationError('请求格式无效')


def valid_email(value):
    if not isinstance(value, str):
        raise ValidationError('请输入有效邮箱')
    value = value.strip().lower()
    validate_email(value)
    if len(value) > 254:
        raise ValidationError('邮箱过长')
    return value


def throttle(request, scene):
    key = f'auth:{scene}:{request.META.get("REMOTE_ADDR", "local")}'
    count = cache.get(key, 0)
    cache.set(key, count + 1, 60)
    return count >= 15

@csrf_protect
@require_http_methods(['POST'])
@sensitive_post_parameters('password', 'password_confirm')
@sensitive_variables('data', 'password')
def register(request):
    if throttle(request, 'register'):
        return JsonResponse({'error': '操作过于频繁，请稍后再试'}, status=429)
    try:
        data = payload(request)
        if set(data) - {'email', 'name', 'password', 'password_confirm', 'verification_code'}:
            raise ValidationError('不支持的注册字段')
        email = valid_email(data.get('email'))
        name = data.get('name', '').strip()
        if not isinstance(data.get('password'), str) or not 1 <= len(name) <= 80:
            raise ValidationError('请输入姓名与密码')
        password = data['password']
        if password != data.get('password_confirm'):
            raise ValidationError('两次密码不一致')
        if len(password) > 256:
            raise ValidationError('密码过长')
        user = User(username=uuid.uuid4().hex, email=email, first_name=name)
        validate_password(password, user)
        with transaction.atomic():
            if User.objects.filter(email=email).exists():
                return JsonResponse({'error': '该邮箱已注册'}, status=400)
            if settings.EMAIL_VERIFICATION_REQUIRED:
                if not mail_verification.consume(request, email, data.get('verification_code')):
                    return JsonResponse({'error': '验证码无效、已使用或过期；最多尝试5次，请重新获取。'}, status=400)
                user.email_verified_at = timezone.now()
            user.set_password(password)
            user.save()
        login_with_fixed_expiry(request, user)
        return JsonResponse({'user': user_json(user), 'csrf': get_token(request)}, status=201)
    except IntegrityError:
        return JsonResponse({'error': '该邮箱已注册'}, status=400)
    except (ValidationError, AttributeError, TypeError) as error:
        return JsonResponse({'error': '；'.join(error.messages) if isinstance(error, ValidationError) else '输入格式无效'}, status=400)

@csrf_protect
@require_http_methods(['POST'])
@sensitive_post_parameters('password')
@sensitive_variables('data')
def sign_in(request):
    if throttle(request, 'login'):
        return JsonResponse({'error': '操作过于频繁，请稍后再试'}, status=429)
    try:
        data = payload(request)
        email = valid_email(data.get('email'))
        password = data.get('password')
        if not isinstance(password, str) or len(password) > 256:
            raise ValidationError('输入格式无效')
        user = User.objects.filter(email=email).first()
        verified = authenticate(request, username=user.username if user else '_unknown_', password=password)
        if not verified:
            return JsonResponse({'error': '邮箱或密码不正确'}, status=400)
        login_with_fixed_expiry(request, verified)
        return JsonResponse({'user': user_json(verified), 'csrf': get_token(request)})
    except ValidationError:
        return JsonResponse({'error': '邮箱或密码不正确'}, status=400)

@csrf_protect
@require_http_methods(['POST'])
def sign_out(request):
    logout(request)
    return JsonResponse({'ok': True, 'csrf': get_token(request)})


def membership(group_id, user):
    m = Membership.objects.filter(group_id=group_id, user=user).first()
    if not m:
        raise NotFound('研究组不存在')
    return m


def mentor(group_id, user):
    m = membership(group_id, user)
    if m.role != 'mentor':
        raise PermissionDenied('仅导师可执行此操作')
    return m

@api_view(['GET', 'POST'])
def groups(request):
    if request.method == 'GET':
        return Response([{'id': m.group_id, 'name': m.group.name, 'role': m.role} for m in Membership.objects.filter(user=request.user).select_related('group')])
    name = request.data.get('name', '')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise APIValidationError('请输入研究组名称')
    with transaction.atomic():
        group = ResearchGroup.objects.create(name=name.strip(), owner=request.user)
        Membership.objects.create(group=group, user=request.user, role='mentor')
    return Response({'id': group.id, 'name': group.name, 'role': 'mentor'}, status=201)

@api_view(['GET', 'POST'])
def members(request, group_id):
    membership(group_id, request.user)
    if request.method == 'POST':
        mentor(group_id, request.user)
        raise APIValidationError('请创建邀请，由学生通过指定 Frienden 空间验证后加入')
    return Response([{**public_user_json(m.user), 'role': m.role} for m in Membership.objects.filter(group_id=group_id).select_related('user')])


def visible_tasks(user):
    return ResearchTask.objects.filter(Q(group__isnull=True, assignees=user) | Q(group__memberships__user=user, assignees=user) | Q(group__memberships__user=user, group__memberships__role='mentor')).distinct()


def task_json(task, user):
    is_mentor = bool(task.group_id and Membership.objects.filter(group_id=task.group_id, user=user, role='mentor').exists())
    entries = task.entries.all() if is_mentor else task.entries.filter(owner=user)
    return {'id': task.id, 'title': task.title, 'description': task.description, 'group_id': task.group_id, 'can_feedback': is_mentor, 'can_write': task.assignees.filter(pk=user.pk).exists(), 'assignees': [public_user_json(u) for u in task.assignees.all()], 'entries': [{'id': e.id, 'owner_id': e.owner_id, 'kind': e.kind, 'day': e.day.isoformat(), 'body': e.body} for e in entries.order_by('-day', '-id')]}

@api_view(['GET', 'POST'])
def tasks(request):
    if request.method == 'GET':
        items = visible_tasks(request.user)
        if 'group_id' in request.query_params:
            try:
                group_id = int(request.query_params['group_id'])
            except (ValueError, TypeError):
                raise APIValidationError('研究组编号无效')
            membership(group_id, request.user)
            items = items.filter(group_id=group_id)
        return Response([task_json(t, request.user) for t in items.order_by('-id')])
    if set(request.data) - {'title', 'description', 'group_id', 'assignee_ids'}:
        raise APIValidationError('不支持的任务字段')
    title = request.data.get('title', '')
    description = request.data.get('description', '')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160 or not isinstance(description, str) or len(description) > 50000:
        raise APIValidationError('任务内容无效')
    group_id = request.data.get('group_id')
    if group_id is not None and type(group_id) is not int:
        raise APIValidationError('研究组编号无效')
    ids = [request.user.id]
    if group_id is not None:
        mentor(group_id, request.user)
        ids = request.data.get('assignee_ids', [])
        if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids) or len(ids) > 100:
            raise APIValidationError('请选择成员')
        if Membership.objects.filter(group_id=group_id, user_id__in=ids).count() != len(set(ids)):
            raise APIValidationError('分配成员必须属于该研究组')
    elif 'assignee_ids' in request.data and request.data['assignee_ids'] != ids:
        raise PermissionDenied('个人任务只能分配给本人')
    with transaction.atomic():
        task = ResearchTask.objects.create(title=title.strip(), description=description, creator=request.user, group_id=group_id)
        task.assignees.set(ids)
    return Response(task_json(task, request.user), status=201)

@api_view(['GET', 'POST'])
def task_detail(request, task_id):
    task = visible_tasks(request.user).filter(pk=task_id).first()
    if not task:
        raise NotFound('任务不存在')
    if 'group_id' in request.query_params and str(task.group_id) != request.query_params['group_id']:
        raise NotFound('任务不属于当前研究组')
    if request.method == 'GET':
        return Response(task_json(task, request.user))
    if set(request.data) - {'kind', 'day', 'body', 'owner_id'}:
        raise APIValidationError('不支持的记录字段')
    kind = request.data.get('kind')
    body = request.data.get('body')
    if kind not in ['report', 'summary', 'feedback'] or not isinstance(body, str) or len(body) > 100000:
        raise APIValidationError('记录内容无效')
    try:
        day = date.fromisoformat(request.data.get('day', date.today().isoformat()))
    except (ValueError, TypeError):
        raise APIValidationError('日期无效')
    owner_id = request.user.id
    if kind == 'feedback':
        if not task.group_id:
            raise PermissionDenied('导师反馈仅用于研究组任务')
        mentor(task.group_id, request.user)
        owner_id = request.data.get('owner_id')
        if type(owner_id) is not int:
            raise APIValidationError('反馈对象无效')
        if not task.assignees.filter(pk=owner_id).exists():
            raise APIValidationError('反馈对象必须是任务成员')
    elif not task.assignees.filter(pk=request.user.id).exists() or request.data.get('owner_id', request.user.id) != request.user.id:
        raise PermissionDenied('只能编辑本人分配任务的记录')
    entry, _ = Entry.objects.update_or_create(task=task, owner_id=owner_id, kind=kind, day=day, defaults={'body': body})
    return Response({'id': entry.id, 'saved': True})

BRAND_ASSETS = {'chronelle-icon-192.png','abandon.svg','favicon-16.png','favicon-32.png','favicon.ico','apple-touch-icon.png','icon-192.png','icon-512.png','icon-maskable-512.png'}
ASSETS = BRAND_ASSETS | {'site.webmanifest','runtime.js', 'ui.js', 'avatars.js', 'ui.css', 'workspace.css', 'style.css', 'account.css', 'account.js', 'calendar.js', 'markdown.js', 'group-workspace.js', 'group-workspace.css', 'workspace-art.svg'}
def asset(request, filename):
    if filename not in ASSETS:
        raise Http404
    return FileResponse((settings.BASE_DIR / ('brand' if filename in BRAND_ASSETS else '') / filename).open('rb'))

def landing(request):
    return FileResponse((settings.BASE_DIR / 'account.html').open('rb'))

def workspace(request):
    if not request.user.is_authenticated:
        return HttpResponseRedirect('/?next=/app/')
    return landing(request)


@api_view(['GET', 'PATCH'])
def profile(request):
    user = request.user
    if request.method == 'PATCH':
        data = request.data
        if set(data) - {'name', 'source', 'preset', 'external_consent', 'gravatar_email_source'}:
            raise APIValidationError('不支持的个人资料字段')
        name = data.get('name', user.first_name)
        source = data.get('source', user.avatar_source)
        preset = data.get('preset', user.avatar_preset)
        email_source = data.get('gravatar_email_source', user.gravatar_email_source)
        if email_source not in ['local', 'friendom']:
            raise APIValidationError('邮箱来源无效')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise APIValidationError('姓名长度应为 1–80 个字符')
        if source == 'friendom' and not (settings.FRIENDOM_ENABLED and FriendomIdentity.objects.filter(user=user).exclude(avatar_url='').exists()):
            return Response({'detail': '请先连接 Frienden 并确认已设置头像'}, status=503)
        if source not in ['preset', 'gravatar', 'friendom'] or type(preset) is not int or not 0 <= preset < 36:
            raise APIValidationError('头像选择无效')
        if source == 'gravatar' and (email_source == 'local' and not user.email or email_source == 'friendom' and not FriendomIdentity.objects.filter(user=user).exclude(gravatar_email_hash='').exists()):
            raise APIValidationError('所选邮箱来源尚不可用')
        if source == 'gravatar' and (user.avatar_source != 'gravatar' or email_source != user.gravatar_email_source) and data.get('external_consent') is not True:
            raise APIValidationError('使用 Gravatar 前需确认将邮箱 SHA-256 哈希发送至 Gravatar')
        user.first_name, user.avatar_source, user.avatar_preset, user.gravatar_email_source = name.strip(), source, preset, email_source
        user.save(update_fields=['first_name', 'avatar_source', 'avatar_preset', 'gravatar_email_source'])
    return Response(user_json(user))

def preset_avatar(request, preset):
    from .preset_avatars import preset_avatar_response
    return preset_avatar_response(preset)


@require_http_methods(['GET'])
def friendom_start(request):
    if not settings.FRIENDOM_ENABLED:
        return JsonResponse({'error': 'Frienden 客户端发布尚未验证'}, status=503)
    if request.get_host() != urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc:
        return HttpResponseRedirect(settings.FRIENDOM_CALLBACK_URL.replace('/callback/', '/start/'))
    from .friendom import approved_adapter
    destination, pending = approved_adapter().begin(scopes=('profile', 'email') if settings.FRIENDOM_EMAIL_ENABLED else ('profile',))
    pending.update({'expires': time.time() + 300, 'user_id': request.user.pk if request.user.is_authenticated else None})
    request.session.pop('group_oauth_pending', None)
    request.session['friendom_pending'] = pending
    return HttpResponseRedirect(destination)

@csrf_protect
@require_http_methods(['POST'])
def friendom_email_start(request):
    if not settings.FRIENDOM_ENABLED or not settings.FRIENDOM_EMAIL_ENABLED:
        return JsonResponse({'error': 'Frienden 邮箱权限尚待服务端上线核验；现有登录不受影响。'}, status=503)
    if not request.user.is_authenticated or request.get_host() != urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc:
        return JsonResponse({'error': '请在本机账号中操作'}, status=403)
    identity = FriendomIdentity.objects.filter(user=request.user).first()
    if not identity:
        return JsonResponse({'error': '请先连接 Frienden 身份'}, status=400)
    try:
        data = payload(request)
        if set(data) != {'external_consent'} or data.get('external_consent') is not True:
            raise ValidationError('需要明确同意邮箱授权和 Gravatar 跨站连接')
    except ValidationError:
        return JsonResponse({'error': '请先勾选隐私说明'}, status=400)
    from .friendom import approved_adapter
    destination, pending = approved_adapter().begin(scopes=('profile', 'email'))
    pending.update({'expires': time.time() + 300, 'user_id': request.user.pk,
                    'purpose': 'gravatar_email', 'subject': identity.subject})
    request.session.pop('group_oauth_pending', None)
    request.session['friendom_pending'] = pending
    return JsonResponse({'authorize_url': destination})

@require_http_methods(['GET'])
@sensitive_variables('pending', 'profile')
def friendom_callback(request):
    if 'group_oauth_pending' in request.session:
        from .group_join import callback
        return callback(request)
    # Consume before validation: one callback attempt per pending flow.
    pending = request.session.pop('friendom_pending', None)
    def failure():
        return HttpResponseRedirect(settings.FRONTEND_ORIGIN + '/?friendom_error=1')
    if not settings.FRIENDOM_ENABLED or request.get_host() != urlsplit(settings.FRIENDOM_CALLBACK_URL).netloc:
        return failure()
    if not pending or pending.get('expires', 0) < time.time() or request.GET.get('error'):
        return failure()
    if any(len(request.GET.getlist(k)) != 1 for k in ['code', 'state']):
        return failure()
    if pending.get('user_id') is not None and (not request.user.is_authenticated or request.user.pk != pending['user_id']):
        return failure()
    from .friendom import approved_adapter, FriendomError
    try:
        email_flow = pending.get('purpose') == 'gravatar_email'
        if email_flow and not settings.FRIENDOM_EMAIL_ENABLED:
            return failure()
        profile = approved_adapter().complete(code=request.GET['code'], state=request.GET['state'], pending=pending)
        with transaction.atomic():
            identity = FriendomIdentity.objects.select_related('user').filter(subject=profile['subject']).first()
            from .models import SpaceIdentity
            space_identity = SpaceIdentity.objects.select_related('user').filter(subject=profile['subject']).first()
            if not email_flow and identity is None and space_identity:
                user = space_identity.user
                if not user.is_active or (pending.get('user_id') is not None and pending['user_id'] != user.pk):
                    raise FriendomError('Identity belongs to a different local account')
                if not request.user.is_authenticated:
                    login_with_fixed_expiry(request, user, backend='django.contrib.auth.backends.ModelBackend')
                return HttpResponseRedirect(settings.FRONTEND_ORIGIN + '/' if settings.FRONTEND_ORIGIN else '/app/')
            if email_flow:
                if not identity or identity.user_id != request.user.pk or profile['subject'] != pending.get('subject'):
                    raise FriendomError('Email grant must match the connected identity')
                digest = profile.get('gravatar_email_hash', '')
                if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                    raise FriendomError('No verified provider email')
                identity.gravatar_email_hash = digest
                identity.gravatar_email_received_at = timezone.now()
                identity.save(update_fields=['gravatar_email_hash', 'gravatar_email_received_at'])
                return HttpResponseRedirect((settings.FRONTEND_ORIGIN + '/' if settings.FRONTEND_ORIGIN else '/app/') + '?friendom_email=ready')
            if pending.get('user_id') is not None:
                user = request.user
                if identity and identity.user_id != user.pk:
                    raise FriendomError('Identity belongs to a different local account')
                existing = FriendomIdentity.objects.filter(user=user).first()
                if existing and existing.subject != profile['subject']:
                    raise FriendomError('Different Frienden identity already connected')
            elif identity:
                user = identity.user
                if not user.is_active:
                    raise FriendomError('Local account inactive')
            else:
                user = User(username=uuid.uuid4().hex, email=None, first_name=profile['name'])
                user.set_unusable_password()
                user.save()
            if identity is None:
                identity = FriendomIdentity.objects.create(user=user, subject=profile['subject'], avatar_url=profile['avatar_url'])
            else:
                identity.avatar_url = profile['avatar_url']
                identity.save(update_fields=['avatar_url'])
            if profile['avatar_url']:
                user.avatar_source = 'friendom'
                user.save(update_fields=['avatar_source'])
        digest = profile.get('gravatar_email_hash', '')
        if 'email' in pending.get('scopes', []) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest):
            identity.gravatar_email_hash = digest
            identity.gravatar_email_received_at = timezone.now()
            identity.save(update_fields=['gravatar_email_hash', 'gravatar_email_received_at'])
        login_with_fixed_expiry(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return HttpResponseRedirect(settings.FRONTEND_ORIGIN + '/' if settings.FRONTEND_ORIGIN else '/app/')
    except (FriendomError, IntegrityError, ValueError, KeyError):
        return failure()
