import json
from datetime import date, timedelta
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
from branch_create.services.branch_tasks import (
    TaskQueryFilters,
    _resolve_date_range,
    collect_pending_tasks,
)


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

    def test_release_tasks_negative_days_back_includes_future(self):
        """days_back < 0 queries from today through today + abs(days)."""
        future_date = timezone.localdate() + timedelta(days=7)
        today = timezone.localdate()
        future_item = self._create_release_item(future_date, "future-project")
        self._create_release_item(today - timedelta(days=10), "past-project")

        tasks = collect_pending_tasks("release", TaskQueryFilters(days_back=-14))
        ids = {t["source_id"] for t in tasks}
        self.assertIn(future_item.id, ids)
        self.assertEqual(len([t for t in tasks if "past-project" in t.get("project_code", "")]), 0)


class ResolveDateRangeTests(TestCase):
    @patch("branch_create.services.branch_tasks.timezone.localdate")
    def test_positive_days_back(self, mock_localdate):
        mock_localdate.return_value = date(2026, 5, 6)
        start, end = _resolve_date_range("", "", 7)
        self.assertEqual(start, date(2026, 4, 29))
        self.assertEqual(end, date(2026, 5, 6))

    @patch("branch_create.services.branch_tasks.timezone.localdate")
    def test_negative_days_back(self, mock_localdate):
        mock_localdate.return_value = date(2026, 5, 6)
        start, end = _resolve_date_range("", "", -7)
        self.assertEqual(start, date(2026, 5, 6))
        self.assertEqual(end, date(2026, 5, 13))

    @patch("branch_create.services.branch_tasks.timezone.localdate")
    def test_zero_days_back(self, mock_localdate):
        mock_localdate.return_value = date(2026, 5, 6)
        start, end = _resolve_date_range("", "", 0)
        d = date(2026, 5, 6)
        self.assertEqual(start, d)
        self.assertEqual(end, d)


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


class HoboCustomBranchSuffixTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="hobo_admin",
            email="hobo_admin@example.com",
            password="pass1234",
        )
        self.project = ProjectCatalog.objects.create(
            project_code="hobo-suffix-project",
            project_name="HOBO Suffix Project",
            enabled=True,
        )

    def _create_payload(self, suffix):
        return {
            "requirement_type": "REQ",
            "project_id": str(self.project.id),
            "description": "custom branch suffix",
            "base_branch": "master",
            "custom_branch_suffix_enabled": "1",
            "custom_branch_suffix": suffix,
        }

    def test_create_hobo_item_appends_custom_branch_suffix(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            "/branch-create/hobo-ledger/api/items/create/",
            self._create_payload("额外名称"),
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertRegex(data["item"]["requirement_branch"], r"^REQ-\d{8}-\d{4}-额外名称$")

    def test_create_hobo_item_rejects_long_custom_branch_suffix(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            "/branch-create/hobo-ledger/api/items/create/",
            self._create_payload("名" * 51),
        )

        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("最多 50 个字", data["error"])


class ApplicantSearchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="search_admin",
            email="search_admin@example.com",
            password="pass1234",
        )
        self.project = ProjectCatalog.objects.create(
            project_code="search-project",
            project_name="Search Project",
            enabled=True,
        )

    def test_hobo_ledger_filters_by_applicant_name(self):
        HoboRequirementLedger.objects.create(
            requirement_type=HoboRequirementLedger.BranchPrefix.REQ,
            requirement_branch="REQ-20260422-9101",
            project=self.project,
            description="match applicant",
            applicant_name="张三",
            applied_date=timezone.localdate(),
            base_branch="master",
            created_by=self.user,
        )
        HoboRequirementLedger.objects.create(
            requirement_type=HoboRequirementLedger.BranchPrefix.REQ,
            requirement_branch="REQ-20260422-9102",
            project=self.project,
            description="other applicant",
            applicant_name="李四",
            applied_date=timezone.localdate(),
            base_branch="master",
            created_by=self.user,
        )

        self.client.force_login(self.user)
        resp = self.client.get(
            "/branch-create/hobo-ledger/api/items/",
            {"applicant_name": "张", "start_date": str(timezone.localdate()), "end_date": str(timezone.localdate())},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["applicant_name"], "张三")

    def test_release_entry_filters_by_applicant_name(self):
        matched_user = User.objects.create_user(
            username="matched_developer",
            first_name="王",
            last_name="五",
            password="pass1234",
        )
        other_user = User.objects.create_user(username="other_developer", password="pass1234")
        batch = ReleaseBatch.objects.create(
            release_date=timezone.localdate(),
            release_type=ReleaseBatch.ReleaseType.RELEASE,
            release_branch="release-search",
            status=ReleaseBatch.Status.OPEN,
            created_by=self.user,
        )
        batch_project = ReleaseBatchProject.objects.create(
            batch=batch,
            project_code="search-project",
            project_name="Search Project",
            enabled=True,
        )
        ReleaseItem.objects.create(
            batch=batch,
            project=batch_project,
            flow_name="matched flow",
            biz_category="biz",
            release_branch=batch.release_branch,
            tech_owner="tech",
            biz_owner="biz",
            developer=matched_user,
        )
        ReleaseItem.objects.create(
            batch=batch,
            project=batch_project,
            flow_name="other flow",
            biz_category="biz",
            release_branch=batch.release_branch,
            tech_owner="tech",
            biz_owner="biz",
            developer=other_user,
        )

        self.client.force_login(self.user)
        resp = self.client.get(
            "/branch-create/release-entry/api/items/",
            {"batch_id": str(batch.id), "applicant_name": "王"},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["developer"], "matched_developer")

    def test_branch_task_query_filters_by_applicant_name(self):
        matched_user = User.objects.create_user(
            username="task_matched_developer",
            first_name="赵",
            last_name="六",
            password="pass1234",
        )
        other_user = User.objects.create_user(username="task_other_developer", password="pass1234")
        today = timezone.localdate()
        HoboRequirementLedger.objects.create(
            requirement_type=HoboRequirementLedger.BranchPrefix.REQ,
            requirement_branch="REQ-20260422-9201",
            project=self.project,
            description="matched hobo",
            applicant_name="赵六",
            applied_date=today,
            base_branch="master",
            created_by=self.user,
        )
        HoboRequirementLedger.objects.create(
            requirement_type=HoboRequirementLedger.BranchPrefix.REQ,
            requirement_branch="REQ-20260422-9202",
            project=self.project,
            description="other hobo",
            applicant_name="孙七",
            applied_date=today,
            base_branch="master",
            created_by=self.user,
        )
        batch = ReleaseBatch.objects.create(
            release_date=today,
            release_type=ReleaseBatch.ReleaseType.RELEASE,
            release_branch="release-task-search",
            status=ReleaseBatch.Status.OPEN,
            created_by=self.user,
        )
        batch_project = ReleaseBatchProject.objects.create(
            batch=batch,
            project_code="task-search-project",
            project_name="Task Search Project",
            enabled=True,
        )
        ReleaseItem.objects.create(
            batch=batch,
            project=batch_project,
            flow_name="matched task flow",
            biz_category="biz",
            release_branch=batch.release_branch,
            tech_owner="tech",
            biz_owner="biz",
            developer=matched_user,
        )
        ReleaseItem.objects.create(
            batch=batch,
            project=batch_project,
            flow_name="other task flow",
            biz_category="biz",
            release_branch=batch.release_branch,
            tech_owner="tech",
            biz_owner="biz",
            developer=other_user,
        )

        tasks = collect_pending_tasks(
            "both",
            TaskQueryFilters(
                start_date=str(today),
                end_date=str(today),
                applicant_name="赵",
            ),
        )

        self.assertEqual(len(tasks), 2)
        self.assertEqual({task["source_type"] for task in tasks}, {"hobo", "release"})
        self.assertTrue(all("赵" in task["applicant_name"] for task in tasks))
