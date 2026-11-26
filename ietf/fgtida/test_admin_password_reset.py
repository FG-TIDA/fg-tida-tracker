from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from . import tests as native_tests


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_MAIL_ENABLED=False)
class AdminPasswordResetTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def test_superuser_can_open_password_form_and_change_another_users_password(self):
        member = get_user_model().objects.create_user('password-reset-member', password='Original!Password42')
        path = reverse('admin:auth_user_password_change', args=[member.pk])
        self.assertContains(self.client.get(path), 'password1')
        weak = self.client.post(path, {'password1': '123', 'password2': '123'})
        self.assertEqual(weak.status_code, 200)
        member.refresh_from_db()
        self.assertTrue(member.check_password('Original!Password42'))
        new_password = 'Quartz!Harbor79-CloudLighthouse'
        result = self.client.post(path, {'password1': new_password, 'password2': new_password})
        self.assertEqual(result.status_code, 302)
        member.refresh_from_db()
        self.assertTrue(member.check_password(new_password))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('test-password'))
        from django.contrib.admin.models import LogEntry, CHANGE
        self.assertTrue(LogEntry.objects.filter(user=self.user, object_id=str(member.pk), action_flag=CHANGE).exists())

    def test_non_superuser_cannot_reset_someone_elses_password(self):
        member = get_user_model().objects.create_user('protected-password-member', password='Original!Password42')
        path = reverse('admin:auth_user_password_change', args=[member.pk])
        from django.contrib.auth.models import Group
        editor = get_user_model().objects.create_user('password-reset-editor', is_staff=True)
        editor.groups.add(Group.objects.get(name='FG-TIDA Administrators'))
        self.client.force_login(editor)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post(path, {'password1': 'Changed!Password42', 'password2': 'Changed!Password42'}).status_code, 403)
        member.refresh_from_db()
        self.assertTrue(member.check_password('Original!Password42'))
        self.client.logout()
        self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.client.post(path, {'password1': 'Changed!Password42', 'password2': 'Changed!Password42'}).status_code, 302)
