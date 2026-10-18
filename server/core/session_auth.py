"""Persistent DB sessions with an explicit deadline fixed at login."""
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import login
from django.utils import timezone


def login_with_fixed_expiry(request,user,backend=None):
    login(request,user,backend=backend)
    # A datetime, not an integer TTL: later session writes keep this deadline.
    request.session.set_expiry(timezone.now()+timedelta(seconds=settings.SESSION_COOKIE_AGE))
