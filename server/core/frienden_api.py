"""Frienden's ordinary account API. No OAuth endpoints or browser credentials.

The space/name/password login is intentionally check-only: a typo must never
create a Frienden account. Passwords are used once and are never persisted.
"""
import gzip
import json
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

ORIGIN = 'https://api.sermo.jyonn.space'
MAX_RESPONSE = 4 * 1024 * 1024


class ProviderError(Exception):
    def __init__(self, code='provider_unavailable', status=502):
        self.code, self.status = code, status
        super().__init__({
            'account_invalid': '空间、昵称或密码不正确，请使用已有账户。',
            'official_required': '导师必须连接此空间的官方账户。',
            'student_required': '学生须连接此空间内自己的个人账户。',
            'space_mismatch': '账户不属于本研究组绑定的空间。',
            'identity_conflict': '此 Frienden 账户已绑定其他 Supervision 账号，或与你已绑定的身份不一致。',
            'connection_required': '请重新连接本空间的 Frienden 账户。',
            'access_denied': '当前账户没有访问或发送此会话的权限。',
            'invalid_response': 'Frienden 返回的数据无法识别，已停止同步。',
            'send_uncertain': '发送结果尚未确认，请同步消息后再决定是否重试。',
            'provider_unavailable': 'Frienden 暂时无法连接，请稍后重试。',
            'invalid_request': '请输入有效的空间、昵称和密码。',
        }.get(code, 'Frienden 暂时无法连接，请稍后重试。'))


def positive_id(value):
    if type(value) not in (str, int) or not re.fullmatch(r'[1-9][0-9]{0,18}', str(value)) or int(value) > 2**63-1:
        raise ProviderError('invalid_response')
    return int(value)


def unpack(value):
    """Decode both provider compact-v1 variants; ordinary JSON is also valid."""
    if not isinstance(value, dict) or value.get('format') != 'compact-v1':
        return value
    try:
        def records(table):
            schemas = table['schemas']
            result = []
            for index, values in table['rows']:
                if type(index) is not int or index < 0: raise ValueError
                keys = schemas[index]
                if not isinstance(keys,list) or not all(isinstance(k,str) for k in keys): raise ValueError
                if len(keys) != len(values): raise ValueError
                result.append(dict(zip(keys, values)))
            return result
        if 'chats' in value:
            users, chats = records(value['users']), records(value['chats'])
            def user_at(index):
                if type(index) is not int or index<0: raise ValueError
                return users[index]
            def message(row):
                if row is None: return None
                row = dict(row)
                if 'user' in row: row['user'] = user_at(row['user'])
                if 'mentions' in row: row['mentions'] = [user_at(i) for i in row['mentions']]
                if isinstance(row.get('reply_to'), dict): row['reply_to'] = message(row['reply_to'])
                return row
            for row in chats:
                row['owner'] = user_at(row['owner']) if row['owner'] is not None else None
                row['members'] = [{**user_at(i), **context} for i, context in row['members']]
                row['last_message'] = message(row['last_message'])
            return chats
        schemas, rows = value['schemas'], value['records']
        budget = [100000]
        def decode(node, depth=0):
            budget[0] -= 1
            if depth > 40 or budget[0] < 0: raise ValueError
            if not isinstance(node, list): return node
            if not node: raise ValueError
            if node[0] == -1: return [decode(v, depth+1) for v in node[1:]]
            if node[0] != -2 or len(node) != 2 or type(node[1]) is not int or node[1] < 0: raise ValueError
            row = rows[node[1]]
            if not isinstance(row,list) or not row or type(row[0]) is not int or row[0]<0: raise ValueError
            keys = schemas[row[0]]
            if not isinstance(keys,list) or not all(isinstance(k,str) for k in keys): raise ValueError
            if len(keys) != len(row)-1: raise ValueError
            return dict(zip(keys, [decode(v, depth+1) for v in row[1:]]))
        return decode(value['root'])
    except (KeyError, ValueError, TypeError, IndexError, RecursionError):
        raise ProviderError('invalid_response') from None


def call(method, path, body=None, token=None, query=None):
    allowed = {
        ('GET', '/spaces/lookup'), ('POST', '/spaces/join'),
        ('GET', '/spaces/me'), ('GET', '/users/me'),
        ('POST', '/users/refresh'), ('POST', '/users/logout'),
        ('GET', '/chats/'), ('POST', '/chats/direct'),
        ('GET', '/messages/'), ('POST', '/messages/'), ('POST', '/chats/read'),
    }
    if (method, path) not in allowed or urlsplit(path).query:
        raise ProviderError('invalid_request', 400)
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'gzip', 'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    req = Request(ORIGIN + path + ('?' + urlencode(query) if query else ''),
                  data=json.dumps(body).encode() if body is not None else None,
                  headers=headers, method=method)
    try:
        with build_opener(NoRedirect).open(req, timeout=8) as response:
            if response.headers.get('Content-Encoding', '').lower() == 'gzip':
                with gzip.GzipFile(fileobj=response) as stream: raw = stream.read(MAX_RESPONSE+1)
            else: raw = response.read(MAX_RESPONSE+1)
            if len(raw) > MAX_RESPONSE: raise ProviderError('invalid_response')
            data = json.loads(raw)
        if not isinstance(data, dict) or 'body' not in data: raise ProviderError('invalid_response')
        # smartdjango packs business failures in the same JSON envelope.
        if data.get('body') is None:
            raise ProviderError('account_invalid' if path == '/spaces/join' else 'access_denied', 400)
        return unpack(data['body'])
    except HTTPError as error:
        if path in ('/spaces/join','/spaces/lookup') and error.code in (400,401,403,404):
            raise ProviderError('account_invalid',400) from None
        raise ProviderError('connection_required' if error.code == 401 else 'access_denied' if error.code in (400, 403, 404) else 'provider_unavailable',
                            401 if error.code == 401 else 403 if error.code in (400, 403, 404) else 502) from None
    except (OSError, ValueError, EOFError):
        raise ProviderError('send_uncertain' if method == 'POST' and path == '/messages/' else 'provider_unavailable') from None


def verify(token, expected_space, official):
    me, space = call('GET', '/users/me', token=token), call('GET', '/spaces/me', token=token)
    if not isinstance(me, dict) or not isinstance(space, dict): raise ProviderError('invalid_response')
    uid, sid = positive_id(me.get('user_id')), positive_id(space.get('space_id'))
    owner = space.get('official_user')
    if not isinstance(owner, dict) or type(me.get('official')) is not bool: raise ProviderError('invalid_response')
    owner_id = positive_id(owner.get('user_id'))
    if sid != expected_space: raise ProviderError('space_mismatch', 403)
    is_official = me['official'] and uid == owner_id
    if official and not is_official: raise ProviderError('official_required', 403)
    if not official and (me['official'] or uid == owner_id): raise ProviderError('student_required', 403)
    if (not isinstance(me.get('name'),str) or not 1<=len(me['name'])<=80
            or not isinstance(space.get('slug'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',space['slug'])
            or not isinstance(space.get('name'),str) or not 1<=len(space['name'])<=200):
        raise ProviderError('invalid_response')
    return {'user_id': uid, 'space_id': sid, 'name': me['name'], 'official': is_official, 'space': space}


def login(slug, name, password, official):
    if (not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', slug)
            or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
            or not isinstance(password, str) or not 1 <= len(password) <= 256):
        raise ProviderError('invalid_request', 400)
    space = call('GET', '/spaces/lookup', query={'slug': slug})
    if not isinstance(space, dict) or space.get('slug') != slug: raise ProviderError('invalid_response')
    sid = positive_id(space.get('space_id'))
    result = call('POST', '/spaces/join', {'slug': slug, 'name': name.strip(), 'password': password,
                                        'language': 'zh-CN', 'new_user_intent': 'check'})
    try:
        auth = result['auth']
        access, refresh = auth['auth'], auth['refresh']
        if not all(isinstance(v, str) and 16 <= len(v) <= 4096 for v in (access, refresh)): raise ValueError
        identity = verify(access, sid, official)
        if result['space']['space_id'] != sid or identity['space']['slug'] != slug: raise ValueError
        return {**identity, 'access': access, 'refresh': refresh}
    except (KeyError, TypeError, ValueError):
        raise ProviderError('invalid_response') from None
