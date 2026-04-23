import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from accounts.models import RoleDefinition, RolePermissionPolicy, UserProfile
from sql_execute.models import SqlExecutionRequest


class SqlApprovalPermissionTests(TestCase):
    def setUp(self):
        self.ops_role = RoleDefinition.get_by_key("ops")
        if not self.ops_role:
            self.ops_role = RoleDefinition.objects.create(
                key="ops",
                name="运维",
                is_system=True,
                enabled=True,
                can_be_registered=True,
                is_staff_role=True,
            )
        self.dev_role = RoleDefinition.get_by_key("developer")
        if not self.dev_role:
            self.dev_role = RoleDefinition.objects.create(
                key="developer",
                name="研发",
                is_system=True,
                enabled=True,
                can_be_registered=True,
                is_staff_role=False,
            )
        RolePermissionPolicy.get_for_role(self.ops_role)
        RolePermissionPolicy.get_for_role(self.dev_role)
        self.superuser = User.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="pass1234",
        )
        self.ops_user = User.objects.create_user(
            username="ops",
            email="ops@example.com",
            password="pass1234",
            is_staff=True,
        )
        UserProfile.objects.create(
            user=self.ops_user,
            role=self.ops_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )
        self.dev_user = User.objects.create_user(
            username="dev",
            email="dev@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=self.dev_user,
            role=self.dev_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )
        self.request_row = SqlExecutionRequest.objects.create(
            release_date=timezone.localdate(),
            folder_path="/tmp/sql",
            selected_files_json=json.dumps(["a.sql"]),
            status=SqlExecutionRequest.Status.PENDING,
            requested_by=self.dev_user,
        )

    def test_ops_user_cannot_approve_sql_request(self):
        self.client.force_login(self.ops_user)
        resp = self.client.post(
            "/sql-execute/api/request/action/",
            {"request_id": self.request_row.id, "action": "approve"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_user_with_sql_edit_others_can_view_other_request_progress(self):
        viewer_role = RoleDefinition.objects.create(
            key="sql_viewer",
            name="SQL观察员",
            enabled=True,
            can_be_registered=False,
            is_staff_role=False,
        )
        viewer_policy = RolePermissionPolicy.get_for_role(viewer_role)
        viewer_policy.menu_sql_execute = True
        viewer_policy.action_sql_request_edit_others = True
        viewer_policy.save(
            update_fields=[
                "menu_sql_execute",
                "action_sql_request_edit_others",
                "updated_at",
            ]
        )
        viewer = User.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=viewer,
            role=viewer_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )

        self.client.force_login(viewer)
        resp = self.client.get(
            f"/sql-execute/api/request/progress/?request_id={self.request_row.id}",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])

    @patch("sql_execute.views._spawn_detached_command")
    def test_superuser_can_approve_and_triggers_background_command(self, mocked_spawn):
        self.client.force_login(self.superuser)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                "/sql-execute/api/request/action/",
                {"request_id": self.request_row.id, "action": "approve"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "running")
        self.request_row.refresh_from_db()
        self.assertEqual(self.request_row.status, SqlExecutionRequest.Status.RUNNING)
        mocked_spawn.assert_called_once()
