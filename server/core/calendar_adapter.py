"""Internal contract only: deliberately no guessed Chronelle HTTP routes/auth."""
from dataclasses import dataclass

class CalendarError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

@dataclass(frozen=True)
class CalendarWriteResult:
    external_id: str
    version: str

class CalendarAdapter:
    """Provider implementation must enforce these semantics before enabling sync.

    availability returns UTC-aware half-open intervals: {start,end,title?}.
    write must deduplicate a stable key, enforce expected_version, and explicitly
    suppress invitations until requested. Uncertain outcomes must be reconcilable
    by idempotency key; callers never generate another create key on timeout.
    """
    def availability(self, connection, start, end, *, details=False):
        raise CalendarError('not_configured')
    def write(self, connection, *, action, payload, idempotency_key, external_id, expected_version):
        raise CalendarError('not_configured')

def calendar_adapter():
    from django.conf import settings
    if settings.CHRONELLE_ENABLED:
        from .calendar_authorization import private_transport,public_transport
        return ChronelleDelegatedAdapter(private_transport,public_transport=public_transport)
    return CalendarAdapter()

class ChronelleDelegatedAdapter(CalendarAdapter):
    """Matches local docs/DELEGATED-API.md; injected transport only, no HTTP setup.

    Transport(connection, method, relative_path, JSON_or_None, headers) returns
    (HTTP_status, JSON_or_None) and must resolve the owner's backend-only bearer.
    Group display MUST use a separate approved sharing_reader, never private
    events or broad delegated authority. No grant issuance/secret loading here.
    """
    prefix='/api/delegated/v1/'
    def __init__(self, transport, sharing_reader=None, public_transport=None):
        self.transport=transport
        self.sharing_reader=sharing_reader
        self.public_transport=public_transport
    def availability(self,connection,start,end,*,details=False):
        if self.sharing_reader is not None:
            return self.sharing_reader(connection,start,end,details=details)
        if self.public_transport is None:
            raise CalendarError('sharing_context_required')
        import uuid
        from urllib.parse import urlencode
        from datetime import date, datetime, time, timezone
        try:reference=str(uuid.UUID(connection.calendar_id))
        except (ValueError,TypeError,AttributeError):raise CalendarError('invalid_calendar_reference') from None
        query=urlencode({'start':start.isoformat(),'end':end.isoformat(),'details':'published' if details else 'busy'})
        # Distinct credentialless transport: no owner cookies, bearer, or share Key.
        try:
            status,data=self.public_transport('GET',f'/api/public/v1/availability/{reference}/?{query}',None,{})
        except (OSError,TimeoutError):
            raise CalendarError('provider_unavailable') from None
        if status!=200 or not isinstance(data,dict) or data.get('calendar_ref')!=reference or data.get('coverage') not in ['complete','partial'] or not isinstance(data.get('intervals'),list):
            raise CalendarError('invalid_public_response')
        result=[]
        for row in data['intervals']:
            if not isinstance(row,dict) or row.get('visibility') not in ['busy','published'] or type(row.get('all_day')) is not bool:
                raise CalendarError('invalid_public_response')
            item={}
            for field in ['start','end']:
                value=row.get(field)
                if not isinstance(value,str):raise CalendarError('invalid_public_response')
                # All-day date intervals have no supplied zone. Conservatively block
                # every possible UTC offset rather than inventing an owner's zone.
                if row['all_day'] and len(value)==10:
                    from datetime import timedelta
                    parsed=datetime.combine(date.fromisoformat(value),time(),timezone.utc)
                    parsed+=timedelta(hours=-14 if field=='start' else 12)
                    item[field]=parsed.isoformat()
                else:item[field]=value
            if details and row['visibility']=='published' and isinstance(row.get('title'),str):item['title']=row['title'][:160]
            result.append(item)
        return {'intervals':result,'complete':data['coverage']=='complete'}
    def request(self,connection,method,route,body=None,key=None):
        headers={'Idempotency-Key':key} if key else {}
        try:
            status,data=self.transport(connection,method,self.prefix+route,body,headers)
        except TimeoutError:
            raise CalendarError('timeout') from None
        errors={401:'grant_unavailable',403:'scope_denied',404:'resource_unavailable',409:'version_conflict',410:'idempotency_expired',400:'invalid_request',429:'throttled'}
        if status not in [200,201,204]:
            raise CalendarError(errors.get(status,'uncertain' if status>=500 else 'invalid_response'))
        return status,data
    def write(self,connection,*,action,payload,idempotency_key,external_id,expected_version):
        import uuid
        if payload.get('send_invitations') is not False:
            raise CalendarError('invitation_consent_required')
        group_id=connection.event_group_id
        if type(group_id) is not int or group_id<1:
            raise CalendarError('event_group_required')
        if action not in ['create','update','cancel']:
            raise CalendarError('unsupported_operation')
        if action!='create':
            try:uuid.UUID(external_id)
            except (ValueError,TypeError,AttributeError):raise CalendarError('invalid_external_id') from None
            if not expected_version:
                raise CalendarError('version_required')
        body={'title':payload['title'],'description':payload['agenda'],'start':payload['start'],
              'end':payload['end'],'timezone':payload['timezone'],'group_id':group_id}
        # Weekly meetings are single occurrences. No RRULE is silently generated.
        if action=='create':method,route='POST','events/'
        elif action=='update':
            method,route='PATCH',f'event/{external_id}/'
            body.update(revision=expected_version,scope='series')
        else:
            method,route='DELETE',f'event/{external_id}/'
            body={'revision':expected_version,'scope':'series'}
        status,data=self.request(connection,method,route,body,idempotency_key)
        if action=='cancel' and status==204:
            return CalendarWriteResult(external_id,expected_version)
        if not isinstance(data,dict) or not isinstance(data.get('id'),str) or not isinstance(data.get('revision'),str) or not data['revision']:
            # A malformed/old provider response must not cause a second creation.
            raise CalendarError('uncertain')
        try:uuid.UUID(data['id'])
        except ValueError:raise CalendarError('uncertain') from None
        if external_id and data['id']!=external_id:
            raise CalendarError('uncertain')
        return CalendarWriteResult(data['id'],data['revision'])
