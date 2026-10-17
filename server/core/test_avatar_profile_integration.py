from django.test import TestCase, Client
from .models import User, FriendomIdentity

class AvatarProfileIntegrationTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user(username='synthetic-avatar-review',email='avatar@example.test',first_name='虚构验证',password='synthetic-test-password')
        self.client.force_login(self.user)

    def test_all_36_select_save_and_resolve_same_preset(self):
        for index in range(36):
            saved=self.client.patch('/api/profile/',{'source':'preset','preset':index},content_type='application/json')
            self.assertEqual(saved.status_code,200)
            self.assertEqual(saved.json()['avatar_preset'],index)
            expected=f'/api/avatars/preset/{index}/'
            self.assertEqual(saved.json()['avatar_url'],expected)
            self.assertEqual(self.client.get('/api/session/').json()['user']['avatar_url'],expected)
            image=Client().get(expected)
            self.assertEqual(image.status_code,200)
            self.assertEqual(image['Content-Type'],'image/png')
            self.assertTrue(image.content.startswith(b'\x89PNG'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar_preset,35)

    def test_reading_presets_does_not_change_external_avatar_source(self):
        FriendomIdentity.objects.create(user=self.user,subject='sermo:1:123',avatar_url='https://api.sermo.jyonn.space/users/avatar/synthetic')
        for source in ['friendom','gravatar']:
            self.user.avatar_source=source;self.user.save(update_fields=['avatar_source'])
            for index in range(36):self.assertEqual(Client().get(f'/api/avatars/preset/{index}/').status_code,200)
            self.user.refresh_from_db();self.assertEqual(self.user.avatar_source,source)
