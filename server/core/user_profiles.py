"""One avatar resolver for the account owner and permission-filtered member views.

Resolving a URL performs no external request and never selects an avatar source.
"""
import hashlib
from .models import FriendomIdentity


def avatar_profile(user, identity=None):
    identity = identity if identity is not None else FriendomIdentity.objects.filter(user=user).first()
    url = f'/api/avatars/preset/{user.avatar_preset}/'
    if user.avatar_source == 'friendom' and identity and identity.avatar_url:
        url = identity.avatar_url
    elif user.avatar_source == 'gravatar':
        digest = (identity.gravatar_email_hash if identity else '') if user.gravatar_email_source == 'friendom' else hashlib.sha256(user.email.strip().lower().encode()).hexdigest() if user.email else ''
        if digest:
            url = f'https://gravatar.com/avatar/{digest}?s=160&d=404&r=g'
    return {'avatar_url': url, 'avatar_preset': user.avatar_preset}


def public_user_json(user):
    # Never use an email address as a missing display-name fallback in shared views.
    return {'id': user.pk, 'name': user.first_name.strip() or '组员', **avatar_profile(user)}
