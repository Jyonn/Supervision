from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.db import transaction
from django.utils import timezone
from .models import CalendarConnection, CalendarSharing, ResearchMeeting, MeetingCalendarLink, MeetingSyncOperation
from .user_profiles import public_user_json
from .calendar_adapter import CalendarError, CalendarWriteResult, calendar_adapter

def interval(start, end, zone='UTC', maximum_days=31):
    try:
        ZoneInfo(zone)
        start = datetime.fromisoformat(start.replace('Z', '+00:00')) if isinstance(start, str) else start
        end = datetime.fromisoformat(end.replace('Z', '+00:00')) if isinstance(end, str) else end
        if not isinstance(start, datetime) or not isinstance(end, datetime) or timezone.is_naive(start) or timezone.is_naive(end):
            raise ValueError
        start, end = start.astimezone(datetime_timezone.utc), end.astimezone(datetime_timezone.utc)
        if end <= start or end-start > timedelta(days=maximum_days):
            raise ValueError
        return start, end
    except (ValueError, TypeError, ZoneInfoNotFoundError, AttributeError):
        raise CalendarError('invalid_interval') from None

def group_availability(group, start, end, adapter=None):
    start, end = interval(start, end)
    adapter = adapter or calendar_adapter()
    rows, merged = [], []
    for member in group.memberships.select_related('user').all():
        user = member.user
        connection = CalendarConnection.objects.filter(user=user, status='active', revoked_at__isnull=True).first()
        sharing = CalendarSharing.objects.filter(user=user, group=group).first()
        row = {**public_user_json(user), 'user_id': user.pk, 'state': 'unknown', 'reason': 'not_shared', 'busy': [], 'all_day': []}
        if connection and sharing and sharing.visibility in ['busy', 'details']:
            try:
                detail = sharing.visibility == 'details'
                projection = adapter.availability(connection, start, end, details=detail)
                complete = projection.get('complete') is True if isinstance(projection,dict) else True
                blocks = projection.get('intervals') if isinstance(projection,dict) else projection
                if not isinstance(blocks, list) or len(blocks) > 1000:
                    raise CalendarError('invalid_response')
                clean, intervals = [], []
                for block in blocks:
                    a,b = interval(block.get('start'), block.get('end'))
                    a,b = max(a,start), min(b,end)
                    if b <= a:
                        continue
                    item = {'start': a.isoformat(), 'end': b.isoformat(), 'all_day': block.get('all_day') is True}
                    if item['all_day']:
                        for field in ('start_date', 'end_date'):
                            value=block.get(field)
                            if isinstance(value,str):
                                from datetime import date
                                if date.fromisoformat(value).isoformat()!=value:
                                    raise CalendarError('invalid_response')
                                item[field]=value
                        item['timezone_uncertain']=block.get('timezone_uncertain') is True
                    if detail:
                        item['title'] = str(block.get('title', '忙碌'))[:160]
                    clean.append(item); intervals.append((a,b))
                merged.extend(intervals)
                row.update(state='known' if complete else 'partial', reason='' if complete else ('all_day_timezone_unknown' if isinstance(projection,dict) and projection.get('uncertainty_reason')=='all_day_timezone_unknown' else 'partial_coverage'), busy=clean, all_day=[item for item in clean if item['all_day']], visibility='details' if detail else 'busy')
            except (CalendarError, ValueError, TypeError, AttributeError):
                row['reason'] = 'provider_unavailable'
        elif not connection:
            row['reason'] = 'not_connected'
        rows.append(row)
    union = []
    for a,b in sorted(merged):
        if union and a <= union[-1][1]:
            union[-1] = (union[-1][0], max(union[-1][1], b))
        else:
            union.append((a,b))
    unknown = [row['user_id'] for row in rows if row['state'] != 'known']
    # Suggestions only when every member supplied authoritative availability.
    suggestions, cursor = [], start
    if not unknown and rows:
        for a,b in union+[(end,end)]:
            if a-cursor >= timedelta(minutes=30):
                suggestions.append({'start': cursor.isoformat(), 'end': a.isoformat()})
            cursor = max(cursor,b)
    return {'members': rows, 'busy_union': [{'start':a.isoformat(),'end':b.isoformat()} for a,b in union],
            'unknown_member_ids': unknown, 'suggestions': suggestions, 'complete': bool(rows) and not unknown}

def queue_sync(meeting, action):
    MeetingSyncOperation.objects.filter(meeting=meeting, status='pending').update(status='superseded')
    payload = {'title': meeting.title, 'agenda': meeting.agenda, 'start': meeting.starts_at.isoformat(),
               'end': meeting.ends_at.isoformat(), 'timezone': meeting.timezone, 'kind': meeting.kind,
               'participant_ids': list(meeting.participants.values_list('id',flat=True)),
               'send_invitations': False}
    return MeetingSyncOperation.objects.create(meeting=meeting, revision=meeting.revision, action=action, payload=payload)

def sync_meeting(meeting_id, actor, expected_revision, adapter=None):
    adapter = adapter or calendar_adapter()
    with transaction.atomic():
        meeting = ResearchMeeting.objects.select_for_update().get(pk=meeting_id)
        if meeting.organizer_id != actor.pk:
            raise CalendarError('forbidden')
        if meeting.revision != expected_revision:
            raise CalendarError('revision_conflict')
        op = MeetingSyncOperation.objects.select_for_update().get(meeting=meeting, revision=meeting.revision)
        if op.status == 'synced':
            return op
        if op.status in ['running', 'conflict', 'superseded']:
            raise CalendarError(op.status)
        connection = CalendarConnection.objects.filter(user=actor, status='active', revoked_at__isnull=True).first()
        if not connection or not connection.provider_subject or not connection.calendar_id:
            raise CalendarError('write_not_authorized')
        link, _ = MeetingCalendarLink.objects.get_or_create(meeting=meeting, defaults={'connection':connection,
            'provider_subject':connection.provider_subject,'calendar_id':connection.calendar_id,'event_group_id':connection.event_group_id})
        if link.connection_id != connection.pk or link.provider_subject!=connection.provider_subject or link.calendar_id!=connection.calendar_id or link.event_group_id!=connection.event_group_id:
            raise CalendarError('connection_conflict')
        if meeting.status == 'cancelled' and not link.external_id:
            # Unsynced local cancellation must never create a remote event.
            op.status='synced';op.save(update_fields=['status','updated_at']);return op
        action = 'cancel' if meeting.status == 'cancelled' else 'update' if link.external_id else 'create'
        required={'create':'calendar.events.create','update':'calendar.events.update','cancel':'calendar.events.delete'}[action]
        if required not in connection.capabilities:
            raise CalendarError('write_not_authorized')
        op.status='running';op.save(update_fields=['status','updated_at'])
        connection_id = connection.pk
        key, payload, external_id, version = str(op.idempotency_key), op.payload, link.external_id, link.external_version
    try:
        result = adapter.write(connection, action=action, payload=payload, idempotency_key=key,
                               external_id=external_id, expected_version=version)
        if not isinstance(result, CalendarWriteResult) or not result.external_id or not result.version or len(result.external_id)>160 or len(result.version)>160:
            raise CalendarError('uncertain')
    except CalendarError as error:
        status = 'conflict' if error.code=='version_conflict' else 'uncertain' if error.code in ['timeout','uncertain','idempotency_expired'] else 'failed'
        MeetingSyncOperation.objects.filter(pk=op.pk).update(status=status,error_code=error.code)
        raise
    except Exception:
        MeetingSyncOperation.objects.filter(pk=op.pk).update(status='uncertain',error_code='uncertain')
        raise CalendarError('uncertain') from None
    with transaction.atomic():
        # A revoke during the request never makes an additional request or invitation.
        MeetingCalendarLink.objects.filter(pk=link.pk, connection_id=connection_id).update(
            external_id=result.external_id, external_version=result.version, synced_revision=expected_revision)
        op.status='synced';op.error_code='';op.save(update_fields=['status','error_code','updated_at'])
    return op
