from django.db import models
from django.db.utils import OperationalError, ProgrammingError
from django.contrib.auth.models import User

from .role_meta import DEFAULT_RELEASE_ENTRY_FIELDS_BY_ROLE_KEY


class RoleDefinition(models.Model):
    class SystemKey:
        OPS = "ops"
        DEVELOPER = "developer"

    key = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=64, unique=True)
    is_system = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    can_be_registered = models.BooleanField(default=False)
    is_staff_role = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "role_definition"
        verbose_name = "角色定义"
        verbose_name_plural = "角色定义"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.name

    @classmethod
    def get_by_key(cls, key: str):
        return cls.objects.filter(key=key).first()

    @classmethod
    def get_default_role(cls):
        role = cls.get_by_key(cls.SystemKey.DEVELOPER)
        if role:
            return role
        return cls.objects.filter(enabled=True).order_by("id").first()


class UserProfile(models.Model):
    class Role:
        OPS = RoleDefinition.SystemKey.OPS
        DEVELOPER = RoleDefinition.SystemKey.DEVELOPER

    class ApprovalStatus(models.TextChoices):
        PENDING = "pending", "待审核"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已拒绝"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.ForeignKey(
        RoleDefinition,
        on_delete=models.PROTECT,
        related_name="profiles",
    )
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    approved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_profiles",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True, default="")

    def __str__(self) -> str:
        role_name = self.role.name if self.role_id else "未设置角色"
        return (
            f"{self.user.username} ({role_name} / "
            f"{self.get_approval_status_display()})"
        )


class GitPlatformConfig(models.Model):
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    git_base_url = models.CharField(max_length=255, default="https://gitlab.spdb.com")
    git_group = models.CharField(max_length=128, default="zh-1087")
    work_base_dir = models.CharField(max_length=255, default="/workspace/repos")
    git_username = models.CharField(max_length=128, blank=True, default="")
    git_password = models.CharField(max_length=255, blank=True, default="")
    git_pat = models.CharField(max_length=255, blank=True, default="")
    sql_repo_path = models.CharField(max_length=255, blank=True, default="")
    sql_repo_clone_url = models.CharField(max_length=255, blank=True, default="")
    sql_db_host = models.CharField(max_length=128, blank=True, default="")
    sql_db_port = models.PositiveIntegerField(default=3306)
    sql_db_name = models.CharField(max_length=128, blank=True, default="")
    sql_db_user = models.CharField(max_length=128, blank=True, default="")
    sql_db_password = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_git_platform_configs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "git_platform_config"
        verbose_name = "Git 平台配置"
        verbose_name_plural = "Git 平台配置"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(singleton_key=1)
        return obj

    @classmethod
    def build_default(cls):
        return cls(
            singleton_key=1,
            git_base_url="https://gitlab.spdb.com",
            git_group="zh-1087",
            work_base_dir="/workspace/repos",
            git_username="",
            git_password="",
            git_pat="",
            sql_repo_path="",
            sql_repo_clone_url="",
            sql_db_host="",
            sql_db_port=3306,
            sql_db_name="",
            sql_db_user="",
            sql_db_password="",
        )

    @classmethod
    def get_solo_safe(cls):
        try:
            obj = cls.get_solo()
            setattr(obj, "_db_ready", True)
            return obj
        except (ProgrammingError, OperationalError):
            obj = cls.build_default()
            setattr(obj, "_db_ready", False)
            return obj

    @staticmethod
    def mask_secret(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        if len(raw) <= 4:
            return "*" * len(raw)
        return f"{raw[:2]}{'*' * (len(raw) - 4)}{raw[-2:]}"


class RolePermissionPolicy(models.Model):
    class DataScope(models.TextChoices):
        SELF = "self", "仅本人"
        ALL = "all", "全部"

    role = models.OneToOneField(
        RoleDefinition,
        on_delete=models.CASCADE,
        related_name="permission_policy",
    )

    # 页面/菜单权限
    menu_release_track = models.BooleanField(default=False)
    menu_branch_create = models.BooleanField(default=False)
    menu_release_entry = models.BooleanField(default=False)
    menu_hobo_ledger = models.BooleanField(default=False)
    menu_sql_execute = models.BooleanField(default=False)
    menu_admin_config = models.BooleanField(default=False)

    # 接口/操作权限
    action_release_track_use = models.BooleanField(default=False)
    action_branch_task_preview = models.BooleanField(default=False)
    action_branch_task_execute_release = models.BooleanField(default=False)
    action_branch_task_execute_hobo = models.BooleanField(default=False)
    action_schedule_manage = models.BooleanField(default=False)
    action_release_batch_manage = models.BooleanField(default=False)
    action_release_item_create = models.BooleanField(default=False)
    action_release_item_edit_dev_fields = models.BooleanField(default=False)
    action_release_item_edit_ops_fields = models.BooleanField(default=False)
    action_release_item_edit_others = models.BooleanField(default=False)
    action_release_item_delete_own = models.BooleanField(default=False)
    action_hobo_item_create = models.BooleanField(default=False)
    action_hobo_item_edit_own = models.BooleanField(default=False)
    action_hobo_item_edit_others = models.BooleanField(default=False)
    action_hobo_item_delete_own = models.BooleanField(default=False)
    action_sql_repo_sync = models.BooleanField(default=False)
    action_sql_request_apply = models.BooleanField(default=False)
    action_sql_request_approve = models.BooleanField(default=False)
    action_sql_request_edit_others = models.BooleanField(default=False)
    release_entry_editable_fields = models.JSONField(default=list, blank=True)

    # 数据范围（仅本人 / 全部）
    data_scope_release_entry = models.CharField(
        max_length=10,
        choices=DataScope.choices,
        default=DataScope.ALL,
    )
    data_scope_hobo_ledger = models.CharField(
        max_length=10,
        choices=DataScope.choices,
        default=DataScope.ALL,
    )
    data_scope_sql_requests = models.CharField(
        max_length=10,
        choices=DataScope.choices,
        default=DataScope.SELF,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "role_permission_policy"
        verbose_name = "角色权限策略"
        verbose_name_plural = "角色权限策略"

    def __str__(self) -> str:
        return f"{self.role.name} 权限策略"

    @classmethod
    def defaults_for_role_key(cls, role_key: str) -> dict:
        if role_key == RoleDefinition.SystemKey.OPS:
            return {
                "menu_release_track": True,
                "menu_branch_create": True,
                "menu_release_entry": True,
                "menu_hobo_ledger": True,
                "menu_sql_execute": True,
                "menu_admin_config": False,
                "action_release_track_use": True,
                "action_branch_task_preview": True,
                "action_branch_task_execute_release": True,
                "action_branch_task_execute_hobo": True,
                "action_schedule_manage": True,
                "action_release_batch_manage": True,
                "action_release_item_create": False,
                "action_release_item_edit_dev_fields": False,
                "action_release_item_edit_ops_fields": True,
                "action_release_item_edit_others": False,
                "action_release_item_delete_own": False,
                "action_hobo_item_create": False,
                "action_hobo_item_edit_own": False,
                "action_hobo_item_edit_others": False,
                "action_hobo_item_delete_own": False,
                "action_sql_repo_sync": False,
                "action_sql_request_apply": False,
                "action_sql_request_approve": False,
                "action_sql_request_edit_others": False,
                "release_entry_editable_fields": DEFAULT_RELEASE_ENTRY_FIELDS_BY_ROLE_KEY.get("ops", []),
                "data_scope_release_entry": cls.DataScope.ALL,
                "data_scope_hobo_ledger": cls.DataScope.ALL,
                "data_scope_sql_requests": cls.DataScope.SELF,
            }
        developer_defaults = {
            "menu_release_track": False,
            "menu_branch_create": False,
            "menu_release_entry": True,
            "menu_hobo_ledger": True,
            "menu_sql_execute": True,
            "menu_admin_config": False,
            "action_release_track_use": False,
            "action_branch_task_preview": False,
            "action_branch_task_execute_release": False,
            "action_branch_task_execute_hobo": False,
            "action_schedule_manage": False,
            "action_release_batch_manage": False,
            "action_release_item_create": True,
            "action_release_item_edit_dev_fields": True,
            "action_release_item_edit_ops_fields": False,
            "action_release_item_edit_others": False,
            "action_release_item_delete_own": True,
            "action_hobo_item_create": True,
            "action_hobo_item_edit_own": True,
            "action_hobo_item_edit_others": False,
            "action_hobo_item_delete_own": True,
            "action_sql_repo_sync": False,
            "action_sql_request_apply": True,
            "action_sql_request_approve": False,
            "action_sql_request_edit_others": False,
            "release_entry_editable_fields": DEFAULT_RELEASE_ENTRY_FIELDS_BY_ROLE_KEY.get(
                "developer", []
            ),
            "data_scope_release_entry": cls.DataScope.ALL,
            "data_scope_hobo_ledger": cls.DataScope.ALL,
            "data_scope_sql_requests": cls.DataScope.SELF,
        }
        if role_key == RoleDefinition.SystemKey.DEVELOPER:
            return developer_defaults

        custom_defaults = developer_defaults.copy()
        custom_defaults.update(
            {
                "menu_release_entry": False,
                "menu_hobo_ledger": False,
                "menu_sql_execute": False,
                "action_release_item_create": False,
                "action_release_item_edit_dev_fields": False,
                "action_release_item_delete_own": False,
                "action_hobo_item_create": False,
                "action_hobo_item_edit_own": False,
                "action_hobo_item_delete_own": False,
                "action_sql_request_apply": False,
                "release_entry_editable_fields": [],
            }
        )
        return custom_defaults

    @classmethod
    def get_for_role(cls, role):
        if not role:
            return None
        role_obj = role if isinstance(role, RoleDefinition) else RoleDefinition.get_by_key(str(role))
        if not role_obj:
            return None
        defaults = cls.defaults_for_role_key(role_obj.key)
        policy, _ = cls.objects.get_or_create(role=role_obj, defaults=defaults)
        return policy

    @classmethod
    def ensure_defaults(cls) -> None:
        for role in RoleDefinition.objects.all():
            cls.get_for_role(role)
