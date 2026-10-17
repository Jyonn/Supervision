import secrets
from datetime import date
from django.test import TestCase, Client, override_settings
from django.core.cache import cache
from django.contrib.sessions.models import Session
from .models import User, ResearchGroup, Membership, ResearchTask, Entry

@override_settings(EMAIL_VERIFICATION_REQUIRED=False)
class AccountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = secrets.token_urlsafe(24)
        self.client = Client(enforce_csrf_checks=True)
    def token(self, client=None):
        return (client or self.client).get('/api/session/').json()['csrf']
    def post(self, path, body=None, client=None):
        client = client or self.client
        return client.post(path, body or {}, content_type='application/json', HTTP_X_CSRFTOKEN=self.token(client))
    def register(self, email='one@example.test', client=None, extra=None):
        return self.post('/api/register/', {'email': email, 'name': '本地测试', 'password': self.password, 'password_confirm': self.password, **(extra or {})}, client)
    def test_registration_hash_and_session(self):
        self.assertEqual(self.register().status_code, 201)
        user = User.objects.get(email='one@example.test')
        self.assertTrue(user.check_password(self.password))
        self.assertNotEqual(user.password, self.password)
        self.assertFalse(user.is_staff)
        self.assertTrue(self.client.get('/api/session/').json()['user'])
        cookie = self.client.cookies['supervision_session']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
    def test_duplicate_email_casefold(self):
        self.register()
        self.assertEqual(self.register('ONE@EXAMPLE.TEST').status_code, 400)
        self.assertEqual(User.objects.count(), 1)
    def test_registration_validation(self):
        for extra in [{'email': 'bad'}, {'password_confirm': 'mismatch'}, {'password': 'short', 'password_confirm': 'short'}, {'name': ''}, {'role': 'mentor'}, {'is_superuser': True}]:
            self.assertEqual(self.register(extra=extra).status_code, 400)
        self.assertFalse(User.objects.exists())
    def test_login_wrong_correct_logout_and_replay(self):
        self.register()
        self.post('/api/logout/')
        self.assertEqual(self.post('/api/login/', {'email': 'one@example.test', 'password': secrets.token_urlsafe(24)}).status_code, 400)
        self.assertIsNone(self.client.get('/api/session/').json()['user'])
        self.assertEqual(self.post('/api/login/', {'email': 'ONE@example.test', 'password': self.password}).status_code, 200)
        old_cookie = self.client.cookies['supervision_session'].value
        self.assertEqual(self.post('/api/logout/').status_code, 200)
        replay = Client(enforce_csrf_checks=True)
        replay.cookies['supervision_session'] = old_cookie
        self.assertIsNone(replay.get('/api/session/').json()['user'])
        self.assertEqual(replay.get('/api/tasks/').status_code, 403)
        self.assertFalse(Session.objects.filter(session_key=old_cookie).exists())
    def test_csrf_register_login_logout(self):
        self.token()
        for path in ['/api/register/', '/api/login/', '/api/logout/']:
            self.assertEqual(self.client.post(path, {}, content_type='application/json').status_code, 403)
        self.register()
        self.assertEqual(self.client.post('/api/tasks/', {'title': 'blocked'}, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post('/api/logout/', {}, content_type='application/json', HTTP_X_CSRFTOKEN=self.token(), HTTP_ORIGIN='https://untrusted.invalid').status_code, 403)
    def test_not_logged_in_and_refresh(self):
        self.assertRedirects(self.client.get('/app/'), '/?next=/app/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/api/tasks/').status_code, 403)
        self.assertEqual(self.client.get('/api/groups/').status_code, 403)
        self.register()
        self.assertEqual(self.client.get('/app/').status_code, 200)
        self.assertIsNotNone(self.client.get('/api/session/').json()['user'])
        self.assertIn('no-store', self.client.get('/api/tasks/')['Cache-Control'])
        self.post('/api/logout/')
        self.assertEqual(self.client.get('/app/').status_code, 302)
    def test_auth_rate_limit(self):
        for _ in range(15):
            self.post('/api/login/', {'email': 'absent@example.test', 'password': self.password})
        self.assertEqual(self.post('/api/login/', {'email': 'absent@example.test', 'password': self.password}).status_code, 429)
    def test_email_services_honest(self):
        from unittest.mock import patch
        with patch('core.mail_verification.ready', return_value=False):
            s = self.client.get('/api/session/').json()
        self.assertFalse(s['email_verification'])
        self.assertFalse(s['password_recovery'])
        from django.conf import settings
        self.assertEqual(s['external_identity'], settings.FRIENDOM_ENABLED)
    def test_private_assets_not_served(self):
        for path in ['/assets/settings.py', '/.local/supervision.sqlite3', '/server/supervision/settings.py']:
            self.assertEqual(self.client.get(path).status_code, 404)

@override_settings(EMAIL_VERIFICATION_REQUIRED=False)
class PermissionTests(TestCase):
    token = AccountTests.token
    post = AccountTests.post
    register = AccountTests.register
    def setUp(self):
        cache.clear()
        self.password = secrets.token_urlsafe(24)
        self.client = Client(enforce_csrf_checks=True)
        self.register()
        self.one = User.objects.get(email='one@example.test')
        self.other = Client(enforce_csrf_checks=True)
        self.register('two@example.test', self.other)
        self.two = User.objects.get(email='two@example.test')
        self.third = Client(enforce_csrf_checks=True)
        self.register('three@example.test', self.third)
        self.three = User.objects.get(email='three@example.test')
    def test_personal_data_isolation(self):
        result = self.post('/api/tasks/', {'title': '私人任务'})
        self.assertEqual(result.status_code, 201)
        task_id = result.json()['id']
        self.assertEqual(self.client.get(f'/api/tasks/{task_id}/').status_code, 200)
        self.assertEqual(self.other.get('/api/tasks/').json(), [])
        self.assertEqual(self.other.get(f'/api/tasks/{task_id}/').status_code, 404)
        self.assertEqual(self.post(f'/api/tasks/{task_id}/', {'kind': 'report', 'body': '试图越权'}, self.other).status_code, 404)
        self.assertEqual(self.post('/api/tasks/', {'title': '伪造分配', 'assignee_ids': [self.two.id]}).status_code, 403)
    def make_group_task(self):
        group = self.post('/api/groups/', {'name': '测试研究组'}).json()
        self.post(f'/api/groups/{group["id"]}/members/', {'email': self.two.email})
        task = self.post('/api/tasks/', {'title': '组任务', 'group_id': group['id'], 'assignee_ids': [self.two.id]}).json()
        return group, task
    def test_membership_and_role_forgery(self):
        group, task = self.make_group_task()
        url = f'/api/groups/{group["id"]}/members/'
        self.assertEqual(self.other.get(url).status_code, 200)
        self.assertEqual(self.third.get(url).status_code, 404)
        self.assertEqual(self.post(url, {'email': self.three.email}, self.other).status_code, 403)
        self.assertEqual(self.post(url, {'email': self.three.email, 'role': 'mentor'}).status_code, 400)
        self.assertEqual(self.post('/api/tasks/', {'title': '伪造导师', 'group_id': group['id'], 'assignee_ids': [self.two.id]}, self.other).status_code, 403)
        self.assertEqual(Membership.objects.get(group_id=group['id'], user=self.two).role, 'student')
    def test_daily_records_summary_and_mentor_feedback(self):
        group, task = self.make_group_task()
        url = f'/api/tasks/{task["id"]}/'
        for kind in ['report', 'summary']:
            self.assertEqual(self.post(url, {'kind': kind, 'body': '学生记录'}, self.other).status_code, 200)
        self.assertEqual(self.post(url, {'kind': 'report', 'body': '伪造本人', 'owner_id': self.two.id}).status_code, 403)
        self.assertEqual(self.post(url, {'kind': 'feedback', 'body': '伪造反馈', 'owner_id': self.two.id}, self.other).status_code, 403)
        self.assertEqual(self.post(url, {'kind': 'feedback', 'body': '导师反馈', 'owner_id': self.two.id}).status_code, 200)
        self.assertEqual(len(self.other.get(url).json()['entries']), 3)
        self.assertEqual(self.third.get(url).status_code, 404)
    def test_student_cannot_see_or_edit_peer_entries(self):
        group, task = self.make_group_task()
        self.post(f'/api/groups/{group["id"]}/members/', {'email': self.three.email})
        model = ResearchTask.objects.get(pk=task['id'])
        model.assignees.add(self.three)
        url = f'/api/tasks/{task["id"]}/'
        self.post(url, {'kind': 'report', 'body': '第二人私密日报'}, self.other)
        self.post(url, {'kind': 'report', 'body': '第三人私密日报'}, self.third)
        self.assertEqual(len(self.other.get(url).json()['entries']), 1)
        self.assertEqual(len(self.client.get(url).json()['entries']), 2)
        self.assertEqual(self.post(url, {'kind': 'report', 'owner_id': self.three.id, 'body': '篡改'}, self.other).status_code, 403)
    def test_group_assignment_validation(self):
        group = self.post('/api/groups/', {'name': '组'}).json()
        self.assertEqual(self.post('/api/tasks/', {'title': '非法成员', 'group_id': group['id'], 'assignee_ids': [self.three.id]}).status_code, 400)

@override_settings(EMAIL_VERIFICATION_REQUIRED=False)
class ProfileTests(TestCase):
    token = AccountTests.token
    post = AccountTests.post
    register = AccountTests.register
    def setUp(self):
        cache.clear()
        self.password = secrets.token_urlsafe(24)
        self.client = Client(enforce_csrf_checks=True)
        self.register()
        self.other = Client(enforce_csrf_checks=True)
        self.register('other@example.test', self.other)
    def patch(self, data, client=None, csrf=True):
        c = client or self.client
        return c.patch('/api/profile/', data, content_type='application/json', **({'HTTP_X_CSRFTOKEN': self.token(c)} if csrf else {}))
    def test_private_profile_and_preset_isolation(self):
        result = self.patch({'source': 'preset', 'preset': 19, 'name': '更新姓名'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get('/api/profile/').json()['avatar_preset'], 19)
        self.assertEqual(self.other.get('/api/profile/').json()['avatar_preset'], 0)
        self.assertNotIn('gravatar.com', self.other.get('/api/profile/').json()['avatar_url'])
        self.assertEqual(self.client.get('/api/session/').json()['user']['name'], '更新姓名')
        self.assertEqual(self.patch({'owner_id': 2, 'preset': 9}).status_code, 400)
    def test_external_avatar_requires_consent(self):
        self.assertEqual(self.patch({'source': 'gravatar'}).status_code, 400)
        response = self.patch({'source': 'gravatar', 'external_consent': True})
        self.assertEqual(response.status_code, 200)
        self.assertIn('https://gravatar.com/avatar/', response.json()['avatar_url'])
        self.assertNotIn('one@example.test', response.json()['avatar_url'])
        self.assertEqual(self.patch({'source': 'friendom'}).status_code, 503)
        self.assertEqual(self.other.get('/api/profile/').json()['avatar_source'], 'preset')
    def test_profile_csrf_auth_and_validation(self):
        self.assertEqual(self.patch({'preset': 3}, csrf=False).status_code, 403)
        self.assertEqual(Client().get('/api/profile/').status_code, 403)
        for data in [{'preset': 36}, {'preset': -1}, {'preset': '1'}, {'source': 'upload'}, {'name': ''}]:
            self.assertEqual(self.patch(data).status_code, 400)
    def test_only_generated_public_avatars(self):
        self.assertEqual(Client().get('/api/avatars/preset/5/').status_code, 200)
        self.assertEqual(Client().get('/api/avatars/preset/36/').status_code, 404)
