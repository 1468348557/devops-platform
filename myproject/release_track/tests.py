from django.contrib.auth.models import User
from django.test import TestCase


class ReleaseTrackPermissionTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="pass1234",
        )
        self.ops_staff = User.objects.create_user(
            username="ops",
            email="ops@example.com",
            password="pass1234",
            is_staff=True,
        )

    def test_superuser_can_access_release_track_page(self):
        self.client.force_login(self.superuser)
        resp = self.client.get("/release-track/")
        self.assertEqual(resp.status_code, 200)

    def test_staff_user_cannot_access_release_track_page(self):
        self.client.force_login(self.ops_staff)
        resp = self.client.get("/release-track/")
        self.assertEqual(resp.status_code, 302)
