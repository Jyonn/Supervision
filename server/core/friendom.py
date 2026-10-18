"""Frienden authorization-code adapter with approved profile-only registration.

Protocol verified against Sermo/User/oauth.py and oauth_views.py. This is a
custom authorization-code/PKCE protocol, not a discovered OIDC provider.
Transport and approved endpoints must be supplied explicitly after registration.
"""
import base64
import hashlib
import re
import secrets
from urllib.parse import urlencode, urlsplit

class FriendomError(ValueError):
    pass

class FriendomAdapter:
    def __init__(self, *, authorize_page, token_url, userinfo_url, client_id, redirect_uri, transport):
        for value in [authorize_page, token_url, userinfo_url]:
            url = urlsplit(value)
            if url.scheme != 'https' or not url.hostname or url.username or url.password:
                raise FriendomError('Provider endpoints must use approved HTTPS URLs')
        callback = urlsplit(redirect_uri)
        if callback.scheme != 'https' and not (callback.scheme == 'http' and callback.hostname in ['127.0.0.1', 'localhost']):
            raise FriendomError('Invalid callback URL')
        if not client_id or not callable(transport):
            raise FriendomError('Client registration and transport required')
        self.authorize_page, self.token_url, self.userinfo_url = authorize_page, token_url, userinfo_url
        self.client_id, self.redirect_uri, self.transport = client_id, redirect_uri, transport
    def begin(self, scopes=('profile',)):
        if tuple(scopes) not in [('profile',), ('profile', 'email')]:
            raise FriendomError('Unsupported local scope request')
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        query = {'client_id': self.client_id, 'redirect_uri': self.redirect_uri, 'scope': ' '.join(scopes), 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'response_type': 'code'}
        # Pending values belong in a short-lived server session, not localStorage.
        return self.authorize_page + '?' + urlencode(query), {'state': state, 'verifier': verifier, 'scopes': list(scopes)}
    def complete(self, *, code, state, pending):
        if not isinstance(state, str) or not secrets.compare_digest(state, pending.get('state', '')):
            raise FriendomError('State mismatch')
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', code):
            raise FriendomError('Invalid code')
        token = self.transport('POST', self.token_url, {'grant_type': 'authorization_code', 'client_id': self.client_id, 'redirect_uri': self.redirect_uri, 'code': code, 'code_verifier': pending['verifier']}, {})
        expected = pending.get('scopes', ['profile'])
        if expected not in [['profile'], ['profile', 'email']]:
            raise FriendomError('Invalid pending scopes')
        if not isinstance(token, dict) or token.get('token_type', '').lower() != 'bearer' or not isinstance(token.get('access_token'), str) or not 1 <= len(token['access_token']) <= 256 or set(token.get('scope', '').split()) != set(expected):
            raise FriendomError('Invalid token response')
        user = self.transport('GET', self.userinfo_url, None, {'Authorization': 'Bearer ' + token['access_token']})
        if not isinstance(user, dict) or not isinstance(user.get('sub'), str) or not re.fullmatch(r'sermo:[1-9][0-9]*:[1-9][0-9]*', user['sub']):
            raise FriendomError('Invalid stable subject')
        # Never automatically link an existing local email account from nickname/avatar.
        avatar = user.get('avatar_url')
        safe_avatar = ''
        if isinstance(avatar, str) and len(avatar) <= 2048:
            parsed = urlsplit(avatar)
            if parsed.scheme == 'https' and parsed.hostname in ['api.sermo.jyonn.space', 'sermo.jyonn.space'] and not parsed.username and not parsed.password and parsed.port in [None, 443]:
                safe_avatar = avatar
        result = {'provider': 'friendom', 'subject': user['sub'], 'name': str(user.get('nickname', 'Frienden 友间用户'))[:80], 'avatar_url': safe_avatar}
        if 'email' in expected:
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError
            email = user.get('email')
            result['gravatar_email_hash'] = ''
            if user.get('email_verified') is True and isinstance(email, str):
                normalized = email.strip().lower()
                try:
                    validate_email(normalized)
                    if len(normalized) <= 254:
                        result['gravatar_email_hash'] = hashlib.sha256(normalized.encode()).hexdigest()
                        result['verified_email'] = normalized
                except ValidationError:
                    pass
        if type(user.get('space_id')) is int and user['space_id'] > 0:
            result['space_id'] = user['space_id']
        if isinstance(user.get('space_slug'), str):
            result['space_slug'] = user['space_slug'][:100]
        if user.get('space_admin') is True:
            result['space_admin'] = True
        return result


def http_transport(method, url, body, headers):
    """Fixed approved HTTPS endpoints, no redirects, bounded JSON; no token logging."""
    import json
    import urllib.request
    import urllib.error
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    approved = {'https://api.sermo.jyonn.space/users/oauth/requests', 'https://api.sermo.jyonn.space/users/oauth/token', 'https://api.sermo.jyonn.space/users/oauth/userinfo'}
    parsed = urlsplit(url)
    from urllib.parse import parse_qs
    import re
    query = parse_qs(parsed.query)
    metadata = (method == 'GET' and parsed.scheme == 'https' and parsed.netloc == 'api.sermo.jyonn.space'
                and parsed.path == '/users/oauth/metadata' and not parsed.fragment
                and set(query) == {'request_id'} and len(query['request_id']) == 1
                and re.fullmatch(r'[A-Za-z0-9_-]{43}', query['request_id'][0]))
    lookup = (method == 'GET' and parsed.scheme == 'https' and parsed.netloc == 'api.sermo.jyonn.space'
              and parsed.path == '/spaces/lookup' and not parsed.fragment and set(query) == {'slug'}
              and len(query['slug']) == 1 and len(query['slug'][0]) <= 100)
    if url not in approved and not metadata and not lookup:
        raise FriendomError('Unapproved provider endpoint')
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Accept': 'application/json', 'Content-Type': 'application/json', **headers}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=12) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise FriendomError('Provider response too large')
            return json.loads(raw)
    except (OSError, ValueError, urllib.error.URLError):
        raise FriendomError('Frienden request failed') from None


def approved_adapter():
    from django.conf import settings
    return FriendomAdapter(authorize_page='https://frienden.jyonn.space/oauth/authorize',
                           token_url='https://api.sermo.jyonn.space/users/oauth/token',
                           userinfo_url='https://api.sermo.jyonn.space/users/oauth/userinfo',
                           client_id=settings.FRIENDOM_CLIENT_ID,
                           redirect_uri=settings.FRIENDOM_CALLBACK_URL,
                           transport=http_transport)
