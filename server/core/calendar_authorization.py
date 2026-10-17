"""Chronelle custom PKCE handoff. No Frienden avatar or legacy key reuse."""
import base64,hashlib,json,re,secrets,time,uuid
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from .calendar_adapter import CalendarError
from .models import CalendarConnection,CalendarCredential,CalendarAuthorizationAttempt,CalendarSharing
from . import calendar_vault

SCOPES=('calendar.identity.read','calendar.events.read','calendar.events.create','calendar.events.update','calendar.events.delete')
def https_origin(value):
    try:
        parsed=urlsplit(value)
        return bool(parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.port in [None,443])
    except ValueError:return False

def configured():
    callback=settings.CHRONELLE_CALLBACK_URL
    return bool(settings.CHRONELLE_AUTH_ENABLED and https_origin(settings.CHRONELLE_APP_URL) and
        https_origin(settings.CHRONELLE_API_ORIGIN) and https_origin(settings.CHRONELLE_AUTHORIZE_ORIGIN) and
        (not callback or https_origin(callback) and urlsplit(callback).netloc==urlsplit(settings.CHRONELLE_APP_URL).netloc))

@sensitive_variables()
def transport(method,path,body=None,headers=None):
    origin=settings.CHRONELLE_API_ORIGIN
    if not https_origin(origin) or urlsplit(origin).path not in ['', '/']:
        raise CalendarError('not_configured')
    if not re.match(r'^/api/(delegated-authorizations/requests/|delegated/v1/|public/v1/availability/)',path):
        raise CalendarError('invalid_route')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args):return None
    request=Request(origin.rstrip('/')+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json',**(headers or {})},method=method)
    try:
        with build_opener(NoRedirect()).open(request,timeout=10) as response:
            raw=response.read(1048577)
            if len(raw)>1048576:raise CalendarError('invalid_response')
            return response.status,json.loads(raw) if raw else None
    except HTTPError as error:
        return error.code,None
    except (OSError,ValueError,URLError):raise CalendarError('provider_unavailable') from None

@sensitive_variables()
def begin(request):
    if not configured():raise CalendarError('not_configured')
    existing=request.session.get('calendar_pending')
    if existing and existing.get('expires',0)>time.time():raise CalendarError('attempt_pending')
    verifier=secrets.token_urlsafe(48);state=secrets.token_urlsafe(32)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    body={'app_name':'Supervision','app_url':settings.CHRONELLE_APP_URL,'purpose':'研究组会议预约与本人日历事件管理；组员可见范围另行选择',
        'code_challenge':challenge,'code_challenge_method':'S256','redirect_uri':settings.CHRONELLE_CALLBACK_URL,
        'state':state,'scopes':list(SCOPES),'all_groups':False,'max_expiry_days':30}
    status,data=transport('POST','/api/delegated-authorizations/requests/',body,{})
    try:
        request_id=str(uuid.UUID(data['request_id']))
        expected=settings.CHRONELLE_AUTHORIZE_ORIGIN.rstrip('/')+'/#/authorize-delegated/'+request_id
        if status!=201 or data.get('authorize_url')!=expected or not https_origin(settings.CHRONELLE_AUTHORIZE_ORIGIN):raise ValueError
    except (KeyError,TypeError,ValueError):raise CalendarError('invalid_response') from None
    request.session['calendar_pending']={'request_id':request_id,'state':state,'verifier':verifier,'user_id':request.user.pk,
        'callback':settings.CHRONELLE_CALLBACK_URL,'expires':time.time()+min(600,data.get('expires_in',600)),'last_poll':0}
    from datetime import timedelta
    CalendarAuthorizationAttempt.objects.create(request_id=request_id,user=request.user,expires_at=timezone.now()+timedelta(seconds=600))
    return {'authorize_url':expected,'poll_interval':3,'pending':True}

def pending_attempt(request):
    pending=request.session.get('calendar_pending')
    if not pending or pending.get('user_id')!=request.user.pk or pending.get('expires',0)<=time.time():
        request.session.pop('calendar_pending',None);raise CalendarError('attempt_expired')
    return pending

@sensitive_variables()
def claim(request,callback=None):
    if not configured():raise CalendarError('not_configured')
    pending=pending_attempt(request)
    if callback is not None:
        if callback.get('request_id')!=pending['request_id'] or not secrets.compare_digest(callback.get('state',''),pending['state']):
            request.session.pop('calendar_pending',None);raise CalendarError('state_mismatch')
        if callback.get('status')!='approved':request.session.pop('calendar_pending',None);raise CalendarError('denied')
    if pending['last_poll']+3>time.time():raise CalendarError('poll_wait')
    pending['last_poll']=time.time();request.session['calendar_pending']=pending
    with transaction.atomic():
        attempt=CalendarAuthorizationAttempt.objects.select_for_update().filter(request_id=pending['request_id'],user=request.user).first()
        if not attempt or attempt.status!='pending' or attempt.expires_at<=timezone.now():raise CalendarError('attempt_unavailable')
        attempt.status='claiming';attempt.save(update_fields=['status'])
    try:status,data=transport('POST',f"/api/delegated-authorizations/requests/{pending['request_id']}/claim/",{'code_verifier':pending['verifier']},{})
    except CalendarError:
        # One-time successful responses cannot be retrieved again after a lost response.
        CalendarAuthorizationAttempt.objects.filter(pk=attempt.pk,status='claiming').update(status='failed')
        request.session.pop('calendar_pending',None);raise CalendarError('claim_uncertain') from None
    if status in [202,429]:
        CalendarAuthorizationAttempt.objects.filter(pk=attempt.pk,status='claiming').update(status='pending')
        if status==202:return {'pending':True,'poll_interval':3}
        raise CalendarError('poll_wait')
    request.session.pop('calendar_pending',None)
    if status!=200:
        CalendarAuthorizationAttempt.objects.filter(pk=attempt.pk,status='claiming').update(status='failed')
        raise CalendarError('denied' if status==403 else 'claim_unavailable')
    try:
        token=data['token'];grant=data['grant'];scopes=grant['scopes'];expires=parse_datetime(grant['expires_at'])
        if (data.get('status')!='approved' or data.get('token_type')!='Bearer' or not isinstance(token,str) or not re.fullmatch(r'cdg_[A-Za-z0-9_-]{43}',token)
            or not secrets.compare_digest(data.get('state',''),pending['state']) or not isinstance(scopes,list)
            or 'calendar.identity.read' not in scopes or not set(scopes)<=set(SCOPES) or not expires or timezone.is_naive(expires) or expires<=timezone.now()
            or grant.get('paused') or grant.get('revoked') or grant.get('all_groups') is not False):raise ValueError
        identity_status,identity=transport('GET','/api/delegated/v1/identity/',None,{'Authorization':'Bearer '+token})
        if identity_status!=200 or not isinstance(identity.get('subject'),str) or not re.fullmatch(r'[1-9][0-9]*',identity['subject']):raise ValueError
        calendar_id=str(uuid.UUID(identity['calendar_ref']));grant_id=str(uuid.UUID(grant['id']))
        if str(uuid.UUID(identity['grant_id']))!=grant_id:raise ValueError
        groups=grant.get('group_ids',[])
        if not isinstance(groups,list) or any(type(i) is not int or i<1 for i in groups):raise ValueError
    except (KeyError,TypeError,ValueError,AttributeError):raise CalendarError('invalid_claim') from None
    with transaction.atomic():
        attempt=CalendarAuthorizationAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.status!='claiming':raise CalendarError('attempt_cancelled')
        connection,_=CalendarConnection.objects.select_for_update().get_or_create(user=request.user)
        if connection.provider_subject and connection.provider_subject!=identity['subject']:
            raise CalendarError('identity_conflict')
        ciphertext=calendar_vault.encrypt(token)
        connection.provider_subject=identity['subject'];connection.calendar_id=calendar_id
        connection.capabilities=scopes;connection.status='active';connection.authorized_at=timezone.now();connection.revoked_at=None
        if connection.event_group_id not in groups:connection.event_group_id=groups[0] if len(groups)==1 else None
        connection.save()
        CalendarCredential.objects.update_or_create(connection=connection,defaults={'ciphertext':ciphertext,'grant_id':grant_id,'expires_at':expires,'group_ids':groups})
        attempt.status='completed';attempt.save(update_fields=['status'])
    return {'connected':True}

@sensitive_variables()
def private_transport(connection,method,path,body,headers):
    token=calendar_vault.token_for(connection)
    status,data=transport(method,path,body,{**headers,'Authorization':'Bearer '+token})
    if status==401:
        CalendarCredential.objects.filter(connection=connection).delete()
        CalendarConnection.objects.filter(pk=connection.pk).update(status='unavailable')
    return status,data

def public_transport(method,path,body,headers):
    if headers or body is not None:raise CalendarError('invalid_public_request')
    return transport(method,path,None,{})
