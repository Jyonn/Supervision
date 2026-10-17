"""Non-secret production Chronelle configuration, restricted to the reviewed origins."""
from django.core.exceptions import ImproperlyConfigured

def chronelle_config(environment):
    expected = {
        'CHRONELLE_API_ORIGIN': 'https://api.chronelle.jyonn.space',
        'CHRONELLE_AUTHORIZE_ORIGIN': 'https://chronelle.jyonn.space',
        'CHRONELLE_APP_URL': 'https://api.supervision.qijiong.work',
        'CHRONELLE_CALLBACK_URL': 'https://api.supervision.qijiong.work/api/calendar/authorization/callback/',
    }
    result = {}
    for name in ['CHRONELLE_ENABLED', 'CHRONELLE_AUTH_ENABLED']:
        value = environment.get('SUPERVISION_' + name, '0')
        if value not in ['0', '1']:
            raise ImproperlyConfigured(name + ' must be 0 or 1')
        result[name] = value == '1'
    if result['CHRONELLE_ENABLED'] and not result['CHRONELLE_AUTH_ENABLED']:
        raise ImproperlyConfigured('Chronelle sync requires its authorization flow')
    enabled = result['CHRONELLE_AUTH_ENABLED']
    for name, approved in expected.items():
        value = environment.get('SUPERVISION_' + name, '')
        if value and value != approved or enabled and value != approved:
            raise ImproperlyConfigured(name + ' must match the reviewed production endpoint')
        result[name] = value
    return result
