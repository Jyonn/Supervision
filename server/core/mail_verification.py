"""Verification-only local adapter. Configuration and secrets never enter responses/logs."""
import hashlib
import json
import os
import secrets
import stat
from datetime import timedelta
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from django.contrib.auth.hashers import make_password, check_password
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from .models import EmailChallenge, EmailRateBucket

CONFIG_PATH = Path.home() / '.config/supervision/notificator.private.json'
HOST = 'https://notice.6-79.cn'
TTL = 600

class MailError(Exception):
    pass

@sensitive_variables()
def configuration():
    try:
        info = CONFIG_PATH.lstat()
        parent = CONFIG_PATH.parent.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            return None
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700 or info.st_size > 8192:
            return None
        with CONFIG_PATH.open() as source:
            data = json.load(source)
        if (data.get('enabled') is not True or data.get('host') != HOST or
            data.get('account') != 'Advisor' or data.get('allowed_purposes') != ['email_verification'] or
            not isinstance(data.get('token'), str) or not data['token'].strip()):
            return None
        return data
    except (OSError, ValueError, TypeError, AttributeError):
        return None

def ready():
    return configuration() is not None

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

@sensitive_variables()
def send_code(email, code):
    config = configuration()
    if not config:
        raise MailError('邮箱验证尚未配置，请使用已有账号或 Friendom。')
    body = {'message': {'format': 'verification', 'body': {'code': code, 'time': 10},
                        'title': 'Supervision 邮箱注册验证', 'locale': 'zh-CN'},
            'deliveries': [{'channel': 'mail', 'target': email, 'options': {}}]}
    request = Request(HOST + '/api/channel/send', data=json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json', 'Auth': 'Advisor$' + config['token']}, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(65537)
            result = json.loads(raw) if len(raw) <= 65536 else None
            if response.status != 200 or not isinstance(result, dict) or result.get('identifier') != 'OK':
                raise ValueError
    except Exception:
        raise MailError('验证邮件发送失败，请稍后重试；未创建账号。') from None

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def session_hash(request):
    if not request.session.session_key:
        request.session.create()
    return digest(request.session.session_key)

def rate_limit(key, limit, seconds):
    now = timezone.now()
    with transaction.atomic():
        bucket, _ = EmailRateBucket.objects.select_for_update().get_or_create(
            key=digest(key), defaults={'expires_at': now + timedelta(seconds=seconds)})
        if bucket.expires_at <= now:
            bucket.count = 0
            bucket.expires_at = now + timedelta(seconds=seconds)
        if bucket.count >= limit:
            raise MailError('发送过于频繁，请稍后重试。')
        bucket.count += 1
        bucket.save()

@sensitive_variables('code')
def issue(request, email):
    if not ready():
        raise MailError('邮箱验证尚未配置，请使用已有账号或 Friendom。')
    owner = session_hash(request)
    rate_limit('email-minute:' + email, 1, 60)
    rate_limit('email-hour:' + email, 5, 3600)
    rate_limit('ip-hour:' + request.META.get('REMOTE_ADDR', 'local'), 20, 3600)
    code = f'{secrets.randbelow(1000000):06d}'
    with transaction.atomic():
        EmailChallenge.objects.filter(email=email, purpose='registration', consumed=False).update(consumed=True)
        challenge = EmailChallenge.objects.create(email=email, session_hash=owner,
            code_hash=make_password(code), expires_at=timezone.now() + timedelta(seconds=TTL))
    try:
        send_code(email, code)
    except MailError:
        EmailChallenge.objects.filter(pk=challenge.pk).update(consumed=True)
        raise
    EmailChallenge.objects.filter(pk=challenge.pk, consumed=False).update(sent=True)

@sensitive_variables('code')
def consume(request, email, code):
    # Called in registration transaction; wrong-attempt writes must commit even on rejection.
    challenge = EmailChallenge.objects.select_for_update().filter(
        email=email, purpose='registration', session_hash=session_hash(request),
        consumed=False, sent=True).order_by('-id').first()
    if not challenge or challenge.expires_at <= timezone.now() or challenge.attempts >= 5:
        return False
    challenge.attempts += 1
    valid = isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit() and check_password(code, challenge.code_hash)
    challenge.consumed = valid or challenge.attempts >= 5
    challenge.save(update_fields=['attempts', 'consumed'])
    return valid
