from __future__ import annotations

from typing import Any

from accounts.models import RolePermissionPolicy, UserProfile


MENU_FIELD_MAP = {
    "release_track": "menu_release_track",
    "branch_create": "menu_branch_create",
    "release_entry": "menu_release_entry",
    "hobo_ledger": "menu_hobo_ledger",
    "sql_execute": "menu_sql_execute",
    "admin_config": "menu_admin_config",
}

ACTION_FIELD_MAP = {
    "release_track_use": "action_release_track_use",
    "branch_task_preview": "action_branch_task_preview",
    "branch_task_execute_release": "action_branch_task_execute_release",
    "branch_task_execute_hobo": "action_branch_task_execute_hobo",
    "schedule_manage": "action_schedule_manage",
    "release_batch_manage": "action_release_batch_manage",
    "release_item_create": "action_release_item_create",
    "release_item_edit_dev_fields": "action_release_item_edit_dev_fields",
    "release_item_edit_ops_fields": "action_release_item_edit_ops_fields",
    "release_item_edit_others": "action_release_item_edit_others",
    "release_item_delete_own": "action_release_item_delete_own",
    "hobo_item_create": "action_hobo_item_create",
    "hobo_item_edit_own": "action_hobo_item_edit_own",
    "hobo_item_edit_others": "action_hobo_item_edit_others",
    "hobo_item_delete_own": "action_hobo_item_delete_own",
    "sql_repo_sync": "action_sql_repo_sync",
    "sql_request_apply": "action_sql_request_apply",
    "sql_request_approve": "action_sql_request_approve",
    "sql_request_edit_others": "action_sql_request_edit_others",
}

DATA_SCOPE_FIELD_MAP = {
    "release_entry": "data_scope_release_entry",
    "hobo_ledger": "data_scope_hobo_ledger",
    "sql_requests": "data_scope_sql_requests",
}


ALWAYS_ALLOWED_OWN_ACTIONS = {
    "release_item_delete_own",
    "hobo_item_edit_own",
    "hobo_item_delete_own",
}


def _is_approved_role_user(user: Any) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(
        profile
        and profile.role_id
        and profile.role.enabled
        and profile.approval_status == UserProfile.ApprovalStatus.APPROVED
    )


def _get_policy_for_user(user: Any) -> RolePermissionPolicy | None:
    if not _is_approved_role_user(user) or user.is_superuser:
        return None
    profile = getattr(user, "profile", None)
    if not profile:
        return None
    return RolePermissionPolicy.get_for_role(profile.role)


def can_access_menu(user: Any, menu_key: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    field_name = MENU_FIELD_MAP.get(menu_key)
    if not field_name:
        return False
    policy = _get_policy_for_user(user)
    return bool(policy and getattr(policy, field_name, False))


def can_do_action(user: Any, action_key: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if action_key in ALWAYS_ALLOWED_OWN_ACTIONS:
        return _is_approved_role_user(user)
    field_name = ACTION_FIELD_MAP.get(action_key)
    if not field_name:
        return False
    policy = _get_policy_for_user(user)
    return bool(policy and getattr(policy, field_name, False))


def get_data_scope(user: Any, scope_key: str) -> str:
    if not getattr(user, "is_authenticated", False):
        return RolePermissionPolicy.DataScope.SELF
    if user.is_superuser:
        return RolePermissionPolicy.DataScope.ALL
    field_name = DATA_SCOPE_FIELD_MAP.get(scope_key)
    if not field_name:
        return RolePermissionPolicy.DataScope.SELF
    policy = _get_policy_for_user(user)
    if not policy:
        return RolePermissionPolicy.DataScope.SELF
    value = getattr(policy, field_name, RolePermissionPolicy.DataScope.SELF)
    if value not in {RolePermissionPolicy.DataScope.SELF, RolePermissionPolicy.DataScope.ALL}:
        return RolePermissionPolicy.DataScope.SELF
    return value


def apply_data_scope(queryset, user: Any, scope_key: str, owner_field: str):
    scope = get_data_scope(user, scope_key)
    if user.is_superuser or scope == RolePermissionPolicy.DataScope.ALL:
        return queryset
    if not owner_field:
        return queryset.none()
    return queryset.filter(**{owner_field: user})
