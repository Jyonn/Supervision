"""Teacher bootstrap consent: no unverified groups and no student-space relaxation."""
import secrets
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from django.test import TestCase, override_settings, Client
from django.utils import timezone
from datetime import timedelta
from django.db import IntegrityError
from .models import User, ResearchGroup, Membership, GroupSpaceBinding, GroupCreationAttempt, SpaceIdentity

@override_settings(FRIENDEN_SPACE_OAUTH_ENABLED=True)
class GroupCreationTests(TestCase):
    host = {'HTTP_HOST': '127.0.0.1:8001'}
    def setUp(self):
        self.user = User.objects.create_user(username='fictional', email='mentor@example.test')
        self.client.force_login(self.user)
        self.key = str(uuid.uuid4())
    def begin(self, key=None, client=None):
        client = client or self.client
        with patch('core.group_join.http_transport', return_value={
            'request_id': 'r'*43, 'expires_in': 1800,
            'authorization_url': 'https://frienden.jyonn.space/oauth/authorize?request_id='+'r'*43}) as transport:
            result = client.post('/api/groups/', {'name': '虚构新组', 'request_key': key or self.key}, content_type='application/json', **self.host)
            if transport.called:
                params = transport.call_args.args[2]
                self.assertNotIn('space_id', params)
                self.assertEqual(params['purpose'], 'space_admin')
                self.assertEqual(params['scope'], 'profile email')
        return result
    def complete(self, profile=None, query=None):
        pending = self.client.session['group_oauth_pending']
        params = {'state': pending['state'], 'code': secrets.token_urlsafe(32)}
        params.update(query or {})
        value = {'subject': 'sermo:21:7', 'space_id': 21, 'space_admin': True,
                 'name': '虚构导师', 'verified_email': self.user.email}
        value.update(profile or {})
        with patch('core.group_join.approved_adapter') as adapter:
            adapter.return_value.complete.return_value = value
            response = self.client.get('/api/auth/friendom/callback/', params, **self.host)
        return response, params
    def assert_no_group(self):
        for model in (ResearchGroup, Membership, GroupSpaceBinding, SpaceIdentity):
            self.assertEqual(model.objects.count(), 0)
    def test_success_is_atomic_and_duplicate_return_is_idempotent(self):
        self.assertEqual(self.begin().status_code, 200)
        self.assert_no_group()
        response, params = self.complete()
        self.assertIn('created=1', response.url)
        group = ResearchGroup.objects.get()
        self.assertEqual(group.owner, self.user)
        self.assertEqual(Membership.objects.get().role, 'mentor')
        self.assertEqual(GroupSpaceBinding.objects.get().space_id, 21)
        replay = self.client.get('/api/auth/friendom/callback/', params, **self.host)
        self.assertNotIn('error', replay.url)
        result = self.begin().json()
        self.assertEqual(result, {'completed': True, 'group_id': group.pk})
        self.assertEqual(ResearchGroup.objects.count(), 1)
    def test_cancel_expiry_state_and_non_admin_leave_only_retryable_draft(self):
        variants = [({}, {'error': 'access_denied'}), ({'space_admin': False}, {}),
                    ({'space_id': 22}, {}), ({'space_id': True}, {}),
                    ({'verified_email': None}, {}), ({}, {'state': 'wrong'})]
        for profile, query in variants:
            self.begin()
            response, _ = self.complete(profile, query)
            self.assertIn('create_error', response.url)
            self.assert_no_group()
        self.begin()
        session = self.client.session
        pending = session['group_oauth_pending']; pending['expires'] = 0
        session['group_oauth_pending'] = pending; session.save()
        response, _ = self.complete()
        self.assertIn('create_error=expired', response.url)
        self.assert_no_group()
        self.assertEqual(GroupCreationAttempt.objects.count(), 1)
        self.begin(); self.complete()
        self.assertEqual(ResearchGroup.objects.count(), 1)
    def test_transaction_failure_rolls_back_all_identity_and_group_rows(self):
        self.begin()
        with patch('core.group_join.GroupSpaceBinding.objects.create', side_effect=IntegrityError('isolated failure')):
            response, _ = self.complete()
        self.assertIn('create_error', response.url)
        self.assert_no_group()
        self.assertIsNone(GroupCreationAttempt.objects.get().group_id)
    def test_cross_session_and_cross_account_cannot_reuse_draft(self):
        self.begin()
        other = Client(); other.force_login(self.user)
        self.assertEqual(self.begin(client=other).status_code, 403)
        session = self.client.session; pending = session['group_oauth_pending']
        stranger = User.objects.create_user(username='stranger', email='other@example.test')
        self.client.force_login(stranger)
        session = self.client.session; session['group_oauth_pending'] = pending; session.save()
        response, _ = self.complete()
        self.assertIn('create_error', response.url)
        self.assert_no_group()
    def test_deleted_group_is_never_recreated_by_old_request(self):
        self.begin(); self.complete()
        ResearchGroup.objects.get().delete()
        self.assertEqual(self.begin().status_code, 404)
        self.assertFalse(ResearchGroup.objects.exists())
    @override_settings(FRIENDEN_SPACE_OAUTH_ENABLED=False)
    def test_disabled_provider_does_not_create_group(self):
        self.assertEqual(self.begin().status_code, 503)
        self.assert_no_group()
    def test_invalid_body_no_numeric_space_or_name_only_bypass(self):
        for payload in ({'name': 'bypass'}, {'name': 'x', 'request_key': self.key, 'space_id': 1},
                        {'name': '', 'request_key': self.key}, {'name': 'x', 'request_key': 'bad'}):
            self.assertEqual(self.client.post('/api/groups/', payload, content_type='application/json').status_code, 400)
        self.assert_no_group()
    def test_csrf_and_anonymous_rejected(self):
        client = Client(enforce_csrf_checks=True); client.force_login(self.user)
        self.assertEqual(self.begin(client=client).status_code, 403)
        self.client.logout()
        self.assertIn(self.begin().status_code, (401,403))
        self.assert_no_group()
