from unittest.mock import patch
from django.test import TestCase
from .models import User, FriendomIdentity, ResearchGroup, Membership, ResearchTask
from .calendar_service import group_availability
from .user_profiles import public_user_json


class MemberAvatarTests(TestCase):
    def setUp(self):
        self.mentor = User.objects.create_user(username='avatar-mentor', email='private-mentor@example.test', first_name='导师')
        self.student = User.objects.create_user(username='avatar-student', email='private-student@example.test', first_name='学生', avatar_preset=35)
        self.outsider = User.objects.create_user(username='avatar-outsider')
        self.group = ResearchGroup.objects.create(name='测试组', owner=self.mentor)
        Membership.objects.create(group=self.group, user=self.mentor, role='mentor')
        Membership.objects.create(group=self.group, user=self.student, role='student')
        self.task = ResearchTask.objects.create(title='测试任务', creator=self.mentor, group=self.group)
        self.task.assignees.add(self.student)
        self.client.force_login(self.mentor)

    def test_same_avatar_across_member_task_and_availability_without_private_fields(self):
        member = next(m for m in self.client.get(f'/api/groups/{self.group.pk}/members/').json() if m['id'] == self.student.pk)
        assignee = self.client.get('/api/tasks/').json()[0]['assignees'][0]
        availability = group_availability(self.group, '2026-10-20T09:00:00Z', '2026-10-20T10:00:00Z')
        available_member = next(m for m in availability['members'] if m['user_id'] == self.student.pk)
        for record in (member, assignee, available_member):
            self.assertEqual(record['avatar_url'], '/api/avatars/preset/35/')
            self.assertEqual(record['avatar_preset'], 35)
            for key in ('email', 'gravatar_email_hash', 'subject', 'avatar_source', 'friendom_connected'):
                self.assertNotIn(key, record)
        self.assertEqual(available_member['state'], 'unknown')
        self.assertFalse(availability['complete'])
        self.assertEqual(availability['suggestions'], [])

    def test_permission_boundaries_unchanged(self):
        self.client.force_login(self.outsider)
        for path in (f'/api/groups/{self.group.pk}/members/', f'/api/groups/{self.group.pk}/calendar/availability/?start=2026-10-20T09:00:00Z&end=2026-10-20T10:00:00Z'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/api/tasks/').json(), [])

    def test_avatar_sources_resolve_only_selected_source_without_network(self):
        identity = FriendomIdentity.objects.create(user=self.student, subject='synthetic:test', avatar_url='https://example.test/avatar.png', gravatar_email_hash='a'*64)
        with patch('urllib.request.urlopen', side_effect=AssertionError('No external avatar fetch')):
            self.assertEqual(public_user_json(self.student)['avatar_url'], '/api/avatars/preset/35/')
            self.student.avatar_source = 'friendom'
            self.assertEqual(public_user_json(self.student)['avatar_url'], identity.avatar_url)
            self.student.avatar_source = 'gravatar'
            self.student.gravatar_email_source = 'friendom'
            self.assertEqual(public_user_json(self.student)['avatar_url'], f'https://gravatar.com/avatar/{"a"*64}?s=160&d=404&r=g')
            self.student.first_name = ''
            self.assertEqual(public_user_json(self.student)['name'], '组员')
            identity.gravatar_email_hash = ''; identity.save()
            self.assertEqual(public_user_json(self.student)['avatar_url'], '/api/avatars/preset/35/')
