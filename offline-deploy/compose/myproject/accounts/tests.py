from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import RoleDefinition, UserProfile


class AdminConfigPermissionTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="pass1234",
        )
        self.staff_user = User.objects.create_user(
            username="ops",
            email="ops@example.com",
            password="pass1234",
            is_staff=True,
        )

    def test_superuser_can_access_admin_config(self):
        self.client.force_login(self.superuser)
        resp = self.client.get("/admin-config/")
        self.assertEqual(resp.status_code, 200)

    def test_staff_user_cannot_access_admin_config(self):
        self.client.force_login(self.staff_user)
        resp = self.client.get("/admin-config/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")


class RegisterDynamicRoleTests(TestCase):
    def setUp(self):
        self.custom_role = RoleDefinition.objects.create(
            key="qa_tester",
            name="测试",
            enabled=True,
            can_be_registered=True,
            is_staff_role=False,
        )

    def test_register_page_contains_custom_role(self):
        resp = self.client.get("/register/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "测试")

    def test_register_with_custom_role_creates_profile(self):
        resp = self.client.post(
            "/register/",
            {
                "username": "qa_user",
                "email": "qa@example.com",
                "role": str(self.custom_role.id),
                "password1": "Pass1234Abcd",
                "password2": "Pass1234Abcd",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/login/")
        user = User.objects.get(username="qa_user")
        self.assertEqual(user.profile.role_id, self.custom_role.id)
        self.assertEqual(user.profile.approval_status, UserProfile.ApprovalStatus.PENDING)
