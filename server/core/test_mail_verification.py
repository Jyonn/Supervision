import secrets
import tempfile
import json
import os
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from django.core.cache import cache
from .models import User, EmailChallenge, EmailRateBucket
from . import mail_verification as mail

@override_settings(EMAIL_VERIFICATION_REQUIRED=True)
class VerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.email = 'verification@example.invalid'
        self.password = secrets.token_urlsafe(24)
        self.code = None
        self.ready = patch.object(mail, 'ready', return_value=True).start()
        self.sender_patcher = patch.object(mail, 'send_code', side_effect=lambda email, code: setattr(self, 'code', code))
        self.sender = self.sender_patcher.start()
        self.addCleanup(patch.stopall)
    def post(self, path, data, client=None):
        client = client or self.client
        token = client.get('/api/session/').json()['csrf']
        return client.post(path, data, content_type='application/json', HTTP_X_CSRFTOKEN=token)
    def issue(self, **extra):
        return self.post('/api/verification/send/', {'email': self.email, 'purpose': 'registration', **extra})
    def register(self, **extra):
        return self.post('/api/register/', {'email': self.email, 'name': '测试', 'password': self.password,
            'password_confirm': self.password, 'verification_code': self.code, **extra})
    def test_verified_registration_hash_single_use(self):
        self.assertEqual(self.issue().status_code, 200)
        challenge = EmailChallenge.objects.get()
        self.assertNotEqual(challenge.code_hash, self.code)
        self.assertEqual(self.register().status_code, 201)
        self.assertIsNotNone(User.objects.get().email_verified_at)
        challenge.refresh_from_db()
        self.assertTrue(challenge.consumed)
        self.assertEqual(self.register().status_code, 400)
    def test_wrong_code_limit_and_no_account(self):
        self.issue()
        for _ in range(5):
            self.assertEqual(self.register(verification_code='notcode').status_code, 400)
        self.assertEqual(self.register().status_code, 400)
        self.assertFalse(User.objects.exists())
        self.assertEqual(EmailChallenge.objects.get().attempts, 5)
    def test_expiry_email_purpose_session_binding(self):
        self.issue()
        self.assertEqual(self.register(email='other@example.invalid').status_code, 400)
        other = Client(enforce_csrf_checks=True)
        body = {'email': self.email, 'name': '测试', 'password': self.password,
                'password_confirm': self.password, 'verification_code': self.code}
        self.assertEqual(self.post('/api/register/', body, other).status_code, 400)
        self.assertEqual(self.issue(purpose='password_reset').status_code, 400)
        EmailChallenge.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.register().status_code, 400)
    def test_cooldown_resend_invalidates_old_code(self):
        self.issue()
        self.assertEqual(self.issue().status_code, 429)
        EmailRateBucket.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.issue().status_code, 200)
        self.assertTrue(EmailChallenge.objects.order_by('id').first().consumed)
        # Even if the RNG repeats, the old challenge is consumed; only latest remains.
        self.assertEqual(EmailChallenge.objects.filter(consumed=False).count(), 1)
        self.assertEqual(self.register().status_code, 201)
    def test_fail_closed_sender_and_unconfigured(self):
        self.sender.side_effect = mail.MailError('发送失败')
        self.assertEqual(self.issue().status_code, 503)
        self.assertTrue(EmailChallenge.objects.get().consumed)
        self.assertFalse(User.objects.exists())
        self.ready.return_value = False
        self.assertEqual(self.issue(email='second@example.invalid').status_code, 503)
        self.assertEqual(self.register().status_code, 400)
    def test_existing_account_unchanged_and_csrf(self):
        user = User.objects.create_user(username='legacy', email=self.email, password=self.password)
        self.assertEqual(self.issue().status_code, 400)
        self.sender.assert_not_called()
        self.assertEqual(self.post('/api/login/', {'email': self.email, 'password': self.password}).status_code, 200)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)
        self.assertEqual(self.client.post('/api/verification/send/', {}, content_type='application/json').status_code, 403)
    def test_hourly_limit_and_scope(self):
        for _ in range(5):
            self.assertEqual(self.issue().status_code, 200)
            EmailRateBucket.objects.filter(key=mail.digest('email-minute:'+self.email)).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.issue().status_code, 429)
    def test_transport_contract_generic_errors_no_redirect(self):
        config = {'token': 'fake-unit-test-only'}
        response = MagicMock(status=200)
        response.read.return_value = b'{"identifier":"OK"}'
        response.__enter__.return_value = response
        self.sender_patcher.stop()
        with patch.object(mail, 'configuration', return_value=config), patch.object(mail, 'build_opener') as network:
            network.return_value.open.return_value = response
            mail.send_code(self.email, '123456')
            request = network.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, mail.HOST+'/api/channel/send')
            self.assertIn(b'"format": "verification"', request.data)
            network.return_value.open.side_effect = RuntimeError('sensitive fake details')
            with self.assertRaisesRegex(mail.MailError, '^验证邮件发送失败'):
                mail.send_code(self.email, '123456')
    def test_configuration_permissions_scope_and_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'notificator.private.json'
            data = {'host': mail.HOST, 'account': 'Advisor', 'token': 'fake-test-only',
                    'enabled': False, 'allowed_purposes': ['email_verification']}
            path.write_text(json.dumps(data))
            os.chmod(path, 0o600)
            with patch.object(mail, 'CONFIG_PATH', path):
                self.assertIsNone(mail.configuration())
                data['enabled'] = True
                path.write_text(json.dumps(data))
                self.assertIsNotNone(mail.configuration())
                os.chmod(path, 0o644)
                self.assertIsNone(mail.configuration())
                os.chmod(path, 0o600)
                data['allowed_purposes'].append('password_recovery')
                path.write_text(json.dumps(data))
                self.assertIsNone(mail.configuration())
