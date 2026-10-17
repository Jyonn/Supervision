"""Only metadata inside the existing grant; never infer names from numeric IDs."""
import re
from django.core.cache import cache
from django.views.decorators.debug import sensitive_variables
from .calendar_adapter import CalendarError

@sensitive_variables()
def authorized_groups(connection,credential,refresh=False):
    from .calendar_authorization import private_transport
    if not credential:return [],'unavailable'
    key=f'chronelle-group-metadata:{connection.pk}:{credential.grant_id}:{','.join(map(str,sorted(credential.group_ids)))}'
    cached=cache.get(key)
    if cached is not None and not refresh:return cached
    if 'calendar.categories.read' not in connection.capabilities:return [],'scope_metadata_unavailable'
    try:
        status,groups=private_transport(connection,'GET','/api/delegated/v1/groups/',None,{})
        if status!=200:raise CalendarError('metadata_unavailable')
        if groups is None:
            result=([],'unavailable')
        else:
            if not isinstance(groups,list):raise CalendarError('invalid_metadata')
            cleaned=[];seen=set()
            for group in groups:
                if not isinstance(group,dict):raise CalendarError('invalid_metadata')
                identifier=group.get('id');name=group.get('name');color=group.get('color','')
                if type(identifier) is not int or identifier not in credential.group_ids:continue
                if identifier in seen or not isinstance(name,str) or not name.strip() or len(name)>160:raise CalendarError('invalid_metadata')
                seen.add(identifier)
                cleaned.append({'id':identifier,'name':name.strip(),'color':color if isinstance(color,str) and re.fullmatch(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})',color) else ''})
            result=(cleaned,'ready' if len(cleaned)==len(credential.group_ids) else 'partial')
    except CalendarError:result=([],'unavailable')
    cache.set(key,result,300 if result[1]=='ready' else 20)
    return result
