from zoneinfo import ZoneInfo
import time
from django.utils import timezone
import uuid
from django.conf import settings
from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from .models import CalendarConnection, CalendarCredential, CalendarAuthorizationAttempt, CalendarSharing, ResearchGroup, Membership, ResearchMeeting
from .calendar_service import interval, group_availability, queue_sync, sync_meeting
from .calendar_adapter import CalendarError

def member(group_id, user):
    if not Membership.objects.filter(group_id=group_id,user=user).exists():
        raise NotFound('研究组不存在')
    return ResearchGroup.objects.get(pk=group_id)

@api_view(['GET','POST'])
def connection(request):
    value = CalendarConnection.objects.filter(user=request.user).first()
    if request.method=='POST':
        if request.data=={'action':'check'}:
            if not value or not settings.CHRONELLE_AUTH_ENABLED:
                return Response({'error':'尚未连接'},status=400)
            from .calendar_authorization import private_transport
            try:
                status,identity=private_transport(value,'GET','/api/delegated/v1/identity/',None,{})
                if status!=200 or identity.get('subject')!=value.provider_subject:
                    CalendarCredential.objects.filter(connection=value).delete();value.status='unavailable';value.save(update_fields=['status'])
            except CalendarError:
                return Response({'error':'暂时无法核对日历连接'},status=503)
        elif request.data.get('action')=='select_group' and set(request.data)=={'action','group_id'}:
            credential=CalendarCredential.objects.filter(connection=value).first() if value else None
            if not credential or credential.expires_at is not None and credential.expires_at<=timezone.now() or type(request.data['group_id']) is not int or request.data['group_id'] not in credential.group_ids:
                raise ValidationError('请选择本人明确授权的类别组')
            value.event_group_id=request.data['group_id'];value.save(update_fields=['event_group_id'])
        elif request.data not in ({'action':'revoke'},{'action':'refresh_metadata'}):
            raise ValidationError('不支持的连接操作')
    if request.method=='POST' and request.data=={'action':'revoke'}:
        with transaction.atomic():
            if value:
                value.status='revoked';value.revoked_at=timezone.now();value.save(update_fields=['status','revoked_at'])
            CalendarSharing.objects.filter(user=request.user).update(visibility='none')
            CalendarCredential.objects.filter(connection=value).delete()
        pending=request.session.pop('calendar_pending',None)
        if pending:CalendarAuthorizationAttempt.objects.filter(user=request.user,request_id=pending['request_id'],status__in=['pending','claiming']).update(status='cancelled')
        # Remote credential revocation is not claimed until a provider adapter exists.
    from .calendar_authorization import configured
    credential=CalendarCredential.objects.filter(connection=value).first() if value else None
    pending=request.session.get('calendar_pending')
    effective_status=value.status if value else 'disconnected'
    if value and value.status=='active' and credential and credential.expires_at is not None and credential.expires_at<=timezone.now():effective_status='expired'
    groups,metadata_status=[],'unavailable'
    if effective_status=='active' and credential and settings.CHRONELLE_AUTH_ENABLED:
        from .calendar_metadata import authorized_groups
        groups,metadata_status=authorized_groups(value,credential,refresh=request.method=='POST' and request.data.get('action')=='refresh_metadata')
        value.refresh_from_db(fields=['status'])
        if value.status!='active':effective_status=value.status
    auth_state='none'
    if pending:
        attempt=CalendarAuthorizationAttempt.objects.filter(user=request.user,request_id=pending.get('request_id')).first()
        auth_state='expired' if pending.get('expires',0)<=time.time() else attempt.status if attempt else 'failed'
    return Response({'status':effective_status,'capabilities':value.capabilities if value and value.status=='active' else [],
        'provider_ready':settings.CHRONELLE_ENABLED,'auth_ready':configured(),'pending':auth_state in ['pending','claiming'],
        'connected_at':value.authorized_at if value else None,'authorization_status':auth_state,'authorization_expires_at':pending.get('expires') if pending else None,
        'available_groups':groups,'group_metadata_status':metadata_status,
        'is_fixture':bool(getattr(settings,'LOCAL_REVIEW_FIXTURE',False)),
        'auth_unavailable_reason':'review_fixture' if getattr(settings,'LOCAL_REVIEW_FIXTURE',False) else '' if configured() else 'not_configured',
        'grant_expires_at':credential.expires_at if credential else None,
        'grant_permanent':bool(credential and credential.expires_at is None),
        'event_group_id':value.event_group_id if value else None,'available_group_ids':credential.group_ids if credential else [],'remote_revocation_supported':False})

@api_view(['POST'])
def authorization_start(request):
    if (set(request.data)-{'consent','include_calendar_names'} or request.data.get('consent') is not True or type(request.data.get('include_calendar_names',False)) is not bool):raise ValidationError('请明确选择前往Chronelle授权')
    from .calendar_authorization import begin
    try:return Response(begin(request,include_metadata=request.data.get('include_calendar_names',False)))
    except CalendarError as error:return Response({'error':'连接请求未完成','code':error.code},status=409 if error.code=='attempt_pending' else 503)

@api_view(['POST'])
def authorization_claim(request):
    if request.data:raise ValidationError('领取不接受浏览器提交凭据')
    from .calendar_authorization import claim
    try:
        result=claim(request)
        return Response(result,status=202 if result.get('pending') else 200)
    except CalendarError as error:return Response({'error':'连接尚未完成，请检查授权或重新发起','code':error.code},status=429 if error.code=='poll_wait' else 409)

@api_view(['POST'])
def authorization_cancel(request):
    if request.data:raise ValidationError('取消不接受额外字段')
    pending=request.session.pop('calendar_pending',None)
    if pending:CalendarAuthorizationAttempt.objects.filter(user=request.user,request_id=pending['request_id'],status__in=['pending','claiming']).update(status='cancelled')
    return Response({'cancelled':True,'remote_revoked':False})

def authorization_callback(request):
    from django.http import HttpResponseRedirect
    from .calendar_authorization import claim,completed_callback
    frontend=getattr(settings,'FRONTEND_ORIGIN','')
    destination=frontend+'/' if frontend else '/app/'
    if request.method!='GET' or not request.user.is_authenticated:
        return HttpResponseRedirect((frontend+'/' if frontend else '/')+'?calendar_error=login')
    configured_callback=settings.CHRONELLE_CALLBACK_URL
    if not configured_callback or request.build_absolute_uri().split('?')[0]!=configured_callback or set(request.GET)!={'request_id','state','status'} or any(len(request.GET.getlist(k))!=1 for k in request.GET):
        return HttpResponseRedirect(destination+'?calendar_error=callback')
    parameters={k:request.GET[k] for k in request.GET}
    if completed_callback(request,parameters):return HttpResponseRedirect(destination+'?calendar_connected=1')
    try:claim(request,callback=parameters)
    except CalendarError as error:
        if error.code in ['attempt_unavailable','poll_wait']:
            pending=request.session.get('calendar_pending')
            import secrets
            if pending and parameters['request_id']==pending.get('request_id') and secrets.compare_digest(parameters['state'],pending.get('state','')) and parameters['status']=='approved':
                attempt=CalendarAuthorizationAttempt.objects.filter(request_id=pending['request_id'],user=request.user).first()
                if attempt and attempt.status=='claiming':return HttpResponseRedirect(destination+'?calendar_pending=1')
        return HttpResponseRedirect(destination+'?calendar_error=claim')
    request.session.pop('calendar_completed_return',None)
    return HttpResponseRedirect(destination+'?calendar_connected=1')

@api_view(['GET','PATCH'])
def sharing(request,group_id):
    member(group_id,request.user)
    if request.method=='PATCH':
        if set(request.data)!={'visibility','consent'} or request.data.get('visibility') not in ['none','busy','details'] or request.data.get('consent') is not True:
            raise ValidationError('请本人明确选择分享范围')
        value,_=CalendarSharing.objects.update_or_create(user=request.user,group_id=group_id,defaults={'visibility':request.data['visibility']})
    else:
        value=CalendarSharing.objects.filter(user=request.user,group_id=group_id).first()
    return Response({'visibility':value.visibility if value else 'none', 'effective':CalendarConnection.objects.filter(user=request.user,status='active',revoked_at__isnull=True).exists()})

@api_view(['GET'])
def availability(request,group_id):
    group=member(group_id,request.user)
    try:
        return Response(group_availability(group,request.query_params.get('start'),request.query_params.get('end')))
    except CalendarError:
        raise ValidationError('请使用含时区的起止时间，范围不超过31天')

def meeting_json(meeting,user):
    link=getattr(meeting,'calendar_link',None)
    operation=meeting.sync_operations.filter(revision=meeting.revision).first()
    owner=meeting.organizer_id==user.pk
    return {'id':meeting.pk,'group_id':meeting.group_id,'organizer_id':meeting.organizer_id,'title':meeting.title,
        'agenda':meeting.agenda,'start':meeting.starts_at.isoformat(),'end':meeting.ends_at.isoformat(),'timezone':meeting.timezone,
        'kind':meeting.kind,'status':meeting.status,'revision':meeting.revision,'participant_ids':list(meeting.participants.values_list('id',flat=True)),
        'can_edit':owner,'sync':{'status':operation.status if operation else 'local','external_linked':bool(link and link.external_id),
        'synced_revision':link.synced_revision if link else 0,'error_code':operation.error_code if owner and operation else '',
        'external_id':link.external_id if owner and link else None,'external_version':link.external_version if owner and link else None}}

def fields(data,group):
    title,agenda=data.get('title'),data.get('agenda','')
    ids=data.get('participant_ids')
    if not isinstance(title,str) or not 1<=len(title.strip())<=160 or not isinstance(agenda,str) or len(agenda)>50000:
        raise ValidationError('会议标题或议程无效')
    if not isinstance(ids,list) or not ids or len(ids)>100 or any(type(i) is not int for i in ids) or Membership.objects.filter(group=group,user_id__in=ids).count()!=len(set(ids)):
        raise ValidationError('参与者必须是本组成员')
    if data.get('kind','meeting') not in ['meeting','weekly']:
        raise ValidationError('会议类型无效')
    try:
        start,end=interval(data.get('start'),data.get('end'),data.get('timezone','UTC'),maximum_days=1)
    except CalendarError:
        raise ValidationError('会议时间须带时区，时长不超过一天')
    return {'title':title.strip(),'agenda':agenda,'starts_at':start,'ends_at':end,'timezone':data.get('timezone','UTC'),'kind':data.get('kind','meeting')},set(ids)

@api_view(['GET','POST'])
def meetings(request,group_id):
    group=member(group_id,request.user)
    if request.method=='GET':
        return Response([meeting_json(m,request.user) for m in ResearchMeeting.objects.filter(group=group).order_by('-starts_at')[:100]])
    if set(request.data)-{'title','agenda','start','end','timezone','kind','participant_ids','request_key'}:
        raise ValidationError('不支持的会议字段')
    values,ids=fields(request.data,group)
    ids.add(request.user.pk)
    try:
        creation_key=uuid.UUID(request.data.get('request_key',''))
    except (ValueError,AttributeError,TypeError):
        raise ValidationError('缺少有效的会议创建幂等键')
    with transaction.atomic():
        existing=ResearchMeeting.objects.filter(creation_key=creation_key).first()
        if existing:
            if existing.organizer_id==request.user.pk and existing.group_id==group.pk and all(getattr(existing,k)==v for k,v in values.items()) and set(existing.participants.values_list('id',flat=True))==ids:
                return Response(meeting_json(existing,request.user))
            return Response({'error':'同一创建操作的内容已改变，请刷新核对'},status=409)
        meeting=ResearchMeeting.objects.create(creation_key=creation_key,group=group,organizer=request.user,**values)
        meeting.participants.set(ids);queue_sync(meeting,'create')
    return Response(meeting_json(meeting,request.user),status=201)

@api_view(['PATCH','POST'])
def meeting_detail(request,meeting_id):
    with transaction.atomic():
        meeting=ResearchMeeting.objects.select_for_update().filter(pk=meeting_id).first()
        if not meeting:
            raise NotFound('会议不存在')
        member(meeting.group_id,request.user)
        if 'group_id' in request.query_params and str(meeting.group_id) != request.query_params['group_id']:
            raise NotFound('会议不属于当前研究组')
        if meeting.organizer_id!=request.user.pk:
            raise PermissionDenied('仅发起人可更新、取消或同步会议')
        if type(request.data.get('revision')) is not int or request.data['revision']!=meeting.revision:
            return Response({'error':'会议已更改，请刷新后重试'},status=409)
        if request.method=='POST':
            if set(request.data)!={'action','revision'} or request.data['action']!='sync':
                raise ValidationError('不支持的同步操作')
        else:
            if meeting.sync_operations.filter(status__in=['running','uncertain']).exists():
                return Response({'error':'外部结果待核实；请先核对同一幂等操作，避免重复邀请'},status=409)
            if meeting.status=='cancelled':
                raise ValidationError('会议已取消')
            if request.data.get('action')=='cancel' and set(request.data)=={'action','revision'}:
                meeting.status='cancelled'
            else:
                if set(request.data)-{'revision','title','agenda','start','end','timezone','kind','participant_ids'}:
                    raise ValidationError('不支持的会议字段')
                values,ids=fields(request.data,meeting.group)
                ids.add(request.user.pk)
                for key,value in values.items():setattr(meeting,key,value)
                meeting.participants.set(ids)
            meeting.revision+=1;meeting.save();queue_sync(meeting,'cancel' if meeting.status=='cancelled' else 'update')
            return Response(meeting_json(meeting,request.user))
    if not settings.CHRONELLE_ENABLED:
        return Response({'error':'Chronelle 写入契约尚未启用；仅已保存本地会议，没有外部事件或邀请'},status=503)
    try:
        sync_meeting(meeting.pk,request.user,request.data['revision'])
    except CalendarError as error:
        return Response({'error':{'version_conflict':'Chronelle事件已被其他操作更改，请先核对版本','timeout':'结果未知；重试使用同一幂等键，不会另建邀请'}.get(error.code,'同步未完成，请检查授权或稍后核对'),'code':error.code},status=409)
    meeting.refresh_from_db()
    return Response(meeting_json(meeting,request.user))
