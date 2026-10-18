from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

class ExactOriginCors:
    """Credentialed CORS for one explicit frontend; CSRF remains enforced separately."""
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        origin = request.headers.get('Origin')
        allowed = origin in getattr(settings, 'CORS_ALLOWED_ORIGINS', [])
        preflight = request.method == 'OPTIONS' and 'Access-Control-Request-Method' in request.headers
        if preflight:
            headers = {h.strip().lower() for h in request.headers.get('Access-Control-Request-Headers', '').split(',') if h.strip()}
            valid = (allowed and request.path.startswith('/api/') and
                     request.headers['Access-Control-Request-Method'] in ['GET', 'POST', 'PATCH', 'DELETE'] and
                     headers <= {'content-type', 'x-csrftoken'})
            response = HttpResponse(status=204 if valid else 403)
        else:
            response = self.get_response(request)
        if request.path.startswith('/api/'):
            patch_vary_headers(response, ['Origin'])
            if allowed and (not preflight or response.status_code == 204):
                response['Access-Control-Allow-Origin'] = origin
                response['Access-Control-Allow-Credentials'] = 'true'
                if preflight:
                    response['Access-Control-Allow-Methods'] = 'GET, POST, PATCH, DELETE'
                    response['Access-Control-Allow-Headers'] = 'Content-Type, X-CSRFToken'
                    response['Access-Control-Max-Age'] = '600'
        return response

class PrivateResponses:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/api/') or request.path in ['/', '/app/']:
            response['Cache-Control'] = 'no-store, private'
            response['Pragma'] = 'no-cache'
        return response


import logging
import re
class OAuthLogRedaction(logging.Filter):
    def filter(self, record):
        def redact(value):
            if not isinstance(value, str):
                return value
            value = re.sub(r'(/api/join/)[^/?\s]+', r'\1[redacted]', value)
            value = re.sub(r'([A-Z]+ /(?:app/)?\?)[^ ]*invite=[^ ]+', r'\1[redacted]', value)
            return re.sub(r'([A-Z]+ /api/(?:auth/friendom/callback/|calendar/authorization/callback/))\?[^ ]+', r'\1?[redacted]', value)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(v) for v in record.args)
        record.msg = redact(record.msg)
        return True


class PreserveLegacySessionDeadline:
    """Pin still-valid pre-policy logins to their existing DB deadline."""
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if request.user.is_authenticated and request.session.get('_session_expiry') is None:
            from django.contrib.sessions.models import Session
            deadline=Session.objects.filter(session_key=request.session.session_key).values_list('expire_date',flat=True).first()
            if deadline is not None:request.session.set_expiry(deadline)
        return self.get_response(request)
