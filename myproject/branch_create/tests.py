import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from accounts.models import RoleDefinition, RolePermissionPolicy, UserProfile
from branch_create.models import (
    BranchTaskExecuteRun,
    HoboRequirementLedger,
    ProjectCatalog,
    ReleaseBatch,
    ReleaseBatchProject,
    ReleaseItem,
)
from branch_create.services.branch_tasks import TaskQueryFilters, collect_pending_tasks


class BranchExecuteStartTests(TestCase):
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
        RolePermissionPolicy.get_for_role(self.ops_role)
        self.operator = User.objects.create_user(
            username="ops_user",
            email="ops@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=self.operator,
            role=self.ops_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )

    @patch("branch_create.views._spawn_detached_command")
    def test_start_execute_creates_run_and_spawns_background_process(self, mocked_spawn):
        self.client.force_login(self.operator)
        payload = [
            {
                "source_type": "hobo",
                "source_id": 1,
                "project_code": "demo-project",
                "new_branch": "REQ-20260422-0001",
                "base_branch": "master",
            }
        ]
        resp = self.client.post(
            "/branch-create/api/branch-tasks/execute/start/",
            data={"tasks": json.dumps(payload)},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["run_id"])
        self.assertEqual(BranchTaskExecuteRun.objects.count(), 1)
        mocked_spawn.assert_called_once()


class BranchTaskQueryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="release_owner",
            email="release_owner@example.com",
            password="pass1234",
        )

    def _create_release_item(self, release_date, project_code):
        batch = ReleaseBatch.objects.create(
            release_date=release_date,
            release_type=ReleaseBatch.ReleaseType.RELEASE,
            release_branch=f"release-{release_date:%Y%m%d}",
            status=ReleaseBatch.Status.OPEN,
            created_by=self.user,
        )
        project = ReleaseBatchProject.objects.create(
            batch=batch,
            project_code=project_code,
            project_name=project_code,
            enabled=True,
        )
        return ReleaseItem.objects.create(
            batch=batch,
            project=project,
            flow_name=f"{project_code} flow",
            biz_category="biz",
            branch_type="REQ",
            requirement_branch=f"REQ-{release_date:%Y%m%d}-0001",
            release_branch=batch.release_branch,
            tech_owner="tech",
            biz_owner="biz",
            developer=self.user,
        )

    def test_release_tasks_filter_by_batch_release_date(self):
        future_date = timezone.localdate() + timedelta(days=7)
        today = timezone.localdate()
        future_item = self._create_release_item(future_date, "future-project")
        self._create_release_item(today, "today-project")

        tasks = collect_pending_tasks(
            "release",
            TaskQueryFilters(
                start_date=str(future_date),
                end_date=str(future_date),
            ),
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["source_id"], future_item.id)
        self.assertEqual(tasks[0]["date"], str(future_date))


class ReleaseEntryCrossOwnerEditTests(TestCase):
    def setUp(self):
        self.custom_role = RoleDefinition.objects.create(
            key="qa_role",
            name="质量保障",
            enabled=True,
            can_be_registered=False,
            is_staff_role=False,
        )
        self.custom_policy = RolePermissionPolicy.get_for_role(self.custom_role)
        self.custom_policy.action_release_item_edit_dev_fields = True
        self.custom_policy.action_release_item_edit_others = True
        self.custom_policy.release_entry_editable_fields = ["flow_name"]
        self.custom_policy.save(
            update_fields=[
                "action_release_item_edit_dev_fields",
                "action_release_item_edit_others",
                "release_entry_editable_fields",
                "updated_at",
            ]
        )

        self.editor = User.objects.create_user(
            username="editor",
            email="editor@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=self.editor,
            role=self.custom_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=self.owner,
            role=self.custom_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )
        self.batch = ReleaseBatch.objects.create(
            release_date=timezone.localdate(),
            release_type=ReleaseBatch.ReleaseType.RELEASE,
            release_branch="release-20260422",
            status=ReleaseBatch.Status.OPEN,
            created_by=self.owner,
        )
        self.batch_project = ReleaseBatchProject.objects.create(
            batch=self.batch,
            project_code="demo-project",
            project_name="Demo Project",
            enabled=True,
        )
        self.item = ReleaseItem.objects.create(
            batch=self.batch,
            project=self.batch_project,
            flow_name="old flow",
            biz_category="biz",
            branch_type="REQ",
            requirement_branch="REQ-20260422-0001",
            release_branch=self.batch.release_branch,
            tech_owner="tech",
            biz_owner="biz_owner",
            developer=self.owner,
        )

    def test_user_with_edit_others_can_update_other_creator_item(self):
        self.client.force_login(self.editor)
        resp = self.client.post(
            "/branch-create/release-entry/api/items/update/",
            {
                "item_id": str(self.item.id),
                "flow_name": "new flow",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.flow_name, "new flow")


class HoboCrossOwnerEditTests(TestCase):
    def setUp(self):
        self.custom_role = RoleDefinition.objects.create(
            key="hobo_editor_role",
            name="HOBO编辑角色",
            enabled=True,
            can_be_registered=False,
            is_staff_role=False,
        )
        self.custom_policy = RolePermissionPolicy.get_for_role(self.custom_role)
        self.custom_policy.menu_hobo_ledger = True
        self.custom_policy.action_hobo_item_edit_others = True
        self.custom_policy.save(
            update_fields=[
                "menu_hobo_ledger",
                "action_hobo_item_edit_others",
                "updated_at",
            ]
        )

        self.editor = User.objects.create_user(
            username="hobo_editor",
            email="hobo_editor@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=self.editor,
            role=self.custom_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )
        self.owner = User.objects.create_user(
            username="hobo_owner",
            email="hobo_owner@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=self.owner,
            role=self.custom_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )
        self.project = ProjectCatalog.objects.create(
            project_code="hobo-demo-project",
            project_name="HOBO Demo Project",
            enabled=True,
        )
        self.entry = HoboRequirementLedger.objects.create(
            requirement_type=HoboRequirementLedger.BranchPrefix.REQ,
            requirement_branch="REQ-20260422-9901",
            project=self.project,
            description="old desc",
            applicant_name="owner",
            applied_date=timezone.localdate(),
            base_branch="master",
            created_by=self.owner,
        )

    def test_user_with_hobo_edit_others_can_update_other_creator_item(self):
        self.client.force_login(self.editor)
        resp = self.client.post(
            "/branch-create/hobo-ledger/api/items/update/",
            {
                "item_id": str(self.entry.id),
                "description": "new desc",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.description, "new desc")
