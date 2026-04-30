import json

from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.db import IntegrityError
from django.db.utils import OperationalError, ProgrammingError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_http_methods

from branch_create.models import ProjectCatalog

from .forms import LoginForm, RegisterForm
from .models import GitPlatformConfig, RoleDefinition, RolePermissionPolicy, UserProfile
from .role_meta import RELEASE_ENTRY_FIELD_OPTIONS
from .permissions import (
    ACTION_FIELD_MAP,
    DATA_SCOPE_FIELD_MAP,
    MENU_FIELD_MAP,
    can_access_menu,
)


class UserLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm

    def form_valid(self, form):
        user = form.get_user()
        if user.is_superuser:
            return super().form_valid(form)

        profile = getattr(user, "profile", None)
        if not profile:
            form.add_error(None, "账户未绑定角色，请联系管理员处理。")
            return self.form_invalid(form)

        if profile.approval_status == UserProfile.ApprovalStatus.PENDING:
            form.add_error(None, "当前账号待审核，审核通过后才能登录。")
            return self.form_invalid(form)
        if profile.approval_status == UserProfile.ApprovalStatus.REJECTED:
            form.add_error(None, "当前账号已被拒绝，请联系审批人。")
            return self.form_invalid(form)

        return super().form_valid(form)

    def get_success_url(self):
        return "/"


class UserLogoutView(LogoutView):
    http_method_names = ["get", "post", "head", "options"]
    next_page = "/login/"

    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect(self.next_page)


class RegisterView(View):
    template_name = "accounts/register.html"

    def get(self, request):
        form = RegisterForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "注册成功，请等待审核通过后登录。")
            return redirect("/login/")
        return render(request, self.template_name, {"form": form})


def _is_approved_ops(user):
    if user.is_superuser:
        return False
    profile = getattr(user, "profile", None)
    if not profile or not profile.role_id:
        return False
    return (
        profile.role.key == RoleDefinition.SystemKey.OPS
        and profile.approval_status == UserProfile.ApprovalStatus.APPROVED
    )


def _is_approved_staff_role(user):
    if user.is_superuser:
        return False
    profile = getattr(user, "profile", None)
    if not profile or not profile.role_id:
        return False
    return (
        profile.role.enabled
        and profile.role.is_staff_role
        and profile.approval_status == UserProfile.ApprovalStatus.APPROVED
    )


def _can_review(user):
    return user.is_superuser or _is_approved_staff_role(user)


def _can_manage_admin_config(user):
    return user.is_superuser


def _can_manage_target_account(manager, target):
    if target.id == manager.id:
        return False
    if target.is_superuser and not manager.is_superuser:
        return False
    return True


def _normalize_git_base_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/")
    return f"https://{value.rstrip('/')}"


def _can_review_target(user, target_profile):
    if user.is_superuser:
        return True
    if not _is_approved_staff_role(user):
        return False
    if not target_profile.role_id:
        return True
    return not target_profile.role.is_staff_role


def _parse_project_codes(raw_codes: str) -> list[str]:
    normalized = raw_codes.replace("，", ",")
    return [code.strip() for code in normalized.split(",") if code.strip()]


def _build_role_policy_view_model(role: RoleDefinition, policy: RolePermissionPolicy) -> dict:
    return {
        "role_id": role.id,
        "role_key": role.key,
        "role_name": role.name,
        "is_system": role.is_system,
        "enabled": role.enabled,
        "can_be_registered": role.can_be_registered,
        "is_staff_role": role.is_staff_role,
        "menu_fields": {
            "menu_release_track": policy.menu_release_track,
            "menu_branch_create": policy.menu_branch_create,
            "menu_release_entry": policy.menu_release_entry,
            "menu_hobo_ledger": policy.menu_hobo_ledger,
            "menu_sql_execute": policy.menu_sql_execute,
            "menu_notification": policy.menu_notification,
        },
        "action_fields": {
            "action_release_track_use": policy.action_release_track_use,
            "action_branch_task_preview": policy.action_branch_task_preview,
            "action_branch_task_execute_release": policy.action_branch_task_execute_release,
            "action_branch_task_execute_hobo": policy.action_branch_task_execute_hobo,
            "action_schedule_manage": policy.action_schedule_manage,
            "action_release_batch_manage": policy.action_release_batch_manage,
            "action_release_item_create": policy.action_release_item_create,
            "action_release_item_edit_dev_fields": policy.action_release_item_edit_dev_fields,
            "action_release_item_edit_ops_fields": policy.action_release_item_edit_ops_fields,
            "action_release_item_edit_others": policy.action_release_item_edit_others,
            "action_release_item_delete_own": policy.action_release_item_delete_own,
            "action_hobo_item_create": policy.action_hobo_item_create,
            "action_hobo_item_edit_own": policy.action_hobo_item_edit_own,
            "action_hobo_item_edit_others": policy.action_hobo_item_edit_others,
            "action_hobo_item_delete_own": policy.action_hobo_item_delete_own,
            "action_sql_repo_sync": policy.action_sql_repo_sync,
            "action_sql_request_apply": policy.action_sql_request_apply,
            "action_sql_request_approve": policy.action_sql_request_approve,
            "action_sql_request_auto_approve": policy.action_sql_request_auto_approve,
            "action_sql_request_edit_others": policy.action_sql_request_edit_others,
        },
        "data_scope_fields": {
            "data_scope_release_entry": policy.data_scope_release_entry,
            "data_scope_hobo_ledger": policy.data_scope_hobo_ledger,
            "data_scope_sql_requests": policy.data_scope_sql_requests,
        },
        "release_entry_editable_fields": policy.release_entry_editable_fields or [],
    }


def _save_role_policies_from_post(request) -> None:
    non_configurable_true_fields = {
        "action_release_item_delete_own",
        "action_hobo_item_edit_own",
        "action_hobo_item_delete_own",
    }
    for role in RoleDefinition.objects.filter(enabled=True).order_by("id"):
        policy = RolePermissionPolicy.get_for_role(role)
        if not policy:
            continue
        for field_name in MENU_FIELD_MAP.values():
            if field_name == "menu_admin_config":
                continue
            raw = request.POST.get(f"role_{role.id}__{field_name}", "").strip().lower()
            setattr(policy, field_name, raw in {"on", "1", "true", "yes"})
        # 通知铃铛（不在 MENU_FIELD_MAP 中）
        raw = request.POST.get(f"role_{role.id}__menu_notification", "").strip().lower()
        policy.menu_notification = raw in {"on", "1", "true", "yes"}
        for field_name in ACTION_FIELD_MAP.values():
            if field_name in non_configurable_true_fields:
                setattr(policy, field_name, True)
                continue
            raw = request.POST.get(f"role_{role.id}__{field_name}", "").strip().lower()
            setattr(policy, field_name, raw in {"on", "1", "true", "yes"})
        for field_name in DATA_SCOPE_FIELD_MAP.values():
            raw_scope = request.POST.get(
                f"role_{role.id}__{field_name}",
                RolePermissionPolicy.DataScope.SELF,
            ).strip()
            if raw_scope not in {
                RolePermissionPolicy.DataScope.SELF,
                RolePermissionPolicy.DataScope.ALL,
            }:
                raw_scope = RolePermissionPolicy.DataScope.SELF
            setattr(policy, field_name, raw_scope)
        valid_release_entry_field_keys = {k for k, _ in RELEASE_ENTRY_FIELD_OPTIONS}
        selected_release_entry_fields = request.POST.getlist(
            f"role_{role.id}__release_entry_editable_fields"
        )
        policy.release_entry_editable_fields = [
            field_key
            for field_key in selected_release_entry_fields
            if field_key in valid_release_entry_field_keys
        ]
        policy.save()


def _build_role_policy_context() -> dict:
    RolePermissionPolicy.ensure_defaults()
    sections = []
    roles = RoleDefinition.objects.order_by("id")
    for role in roles:
        policy = RolePermissionPolicy.get_for_role(role)
        if not policy:
            continue
        sections.append(_build_role_policy_view_model(role, policy))
    return {
        "role_policy_sections": sections,
        "role_definitions": roles,
        "data_scope_choices": RolePermissionPolicy.DataScope.choices,
        "release_entry_field_options": RELEASE_ENTRY_FIELD_OPTIONS,
    }


def _build_role_key(raw: str) -> str:
    cleaned = "".join(ch for ch in (raw or "").strip().lower() if ch.isalnum() or ch == "_")
    return cleaned[:32]


def _create_role_from_post(request) -> tuple[bool, str]:
    key = _build_role_key(request.POST.get("role_key", ""))
    name = (request.POST.get("role_name", "") or "").strip()
    if not key:
        return False, "角色标识不能为空，且仅支持字母数字下划线。"
    if not name:
        return False, "角色名称不能为空。"
    if RoleDefinition.objects.filter(key=key).exists():
        return False, "角色标识已存在，请更换。"
    if RoleDefinition.objects.filter(name=name).exists():
        return False, "角色名称已存在，请更换。"
    role = RoleDefinition.objects.create(
        key=key,
        name=name,
        is_system=False,
        enabled=True,
        can_be_registered=request.POST.get("can_be_registered", "").strip().lower()
        in {"on", "1", "true", "yes"},
        is_staff_role=request.POST.get("is_staff_role", "").strip().lower()
        in {"on", "1", "true", "yes"},
    )
    RolePermissionPolicy.get_for_role(role)
    return True, f"角色 {name} 创建成功。"


def _update_roles_from_post(request) -> tuple[bool, str]:
    roles = RoleDefinition.objects.order_by("id")
    for role in roles:
        enabled = request.POST.get(f"role_{role.id}__enabled", "").strip().lower() in {
            "on",
            "1",
            "true",
            "yes",
        }
        can_be_registered = request.POST.get(
            f"role_{role.id}__can_be_registered", ""
        ).strip().lower() in {"on", "1", "true", "yes"}
        is_staff_role = request.POST.get(f"role_{role.id}__is_staff_role", "").strip().lower() in {
            "on",
            "1",
            "true",
            "yes",
        }
        if role.is_system:
            enabled = True
        role.enabled = enabled
        role.can_be_registered = can_be_registered
        role.is_staff_role = is_staff_role
        role.save(update_fields=["enabled", "can_be_registered", "is_staff_role", "updated_at"])
    return True, "角色属性已更新。"


def _apply_approval_decision(target_profile, reviewer, action, reason=""):
    if action == "approve":
        target_profile.approval_status = UserProfile.ApprovalStatus.APPROVED
        target_profile.approved_by = reviewer
        target_profile.approved_at = timezone.now()
        target_profile.rejection_reason = ""
        target_profile.save(
            update_fields=[
                "approval_status",
                "approved_by",
                "approved_at",
                "rejection_reason",
            ]
        )
        target_profile.user.is_staff = bool(
            target_profile.role_id and target_profile.role.is_staff_role
        )
        target_profile.user.save(update_fields=["is_staff"])
        return

    target_profile.approval_status = UserProfile.ApprovalStatus.REJECTED
    target_profile.approved_by = reviewer
    target_profile.approved_at = timezone.now()
    target_profile.rejection_reason = reason
    target_profile.save(
        update_fields=[
            "approval_status",
            "approved_by",
            "approved_at",
            "rejection_reason",
        ]
    )
    target_profile.user.is_staff = False
    target_profile.user.save(update_fields=["is_staff"])


@login_required
def dashboard(request):
    profile = getattr(request.user, "profile", None)

    # 通知铃铛可见性
    can_see_notification = False
    if profile and profile.role_id:
        policy = RolePermissionPolicy.get_for_role(profile.role)
        if policy and policy.menu_notification:
            can_see_notification = True
    if request.user.is_superuser:
        can_see_notification = True

    return render(
        request,
        "accounts/dashboard.html",
        {
            "profile": profile,
            "can_review_accounts": _can_review(request.user),
            "can_see_notification": can_see_notification,
            "can_menu_release_track": can_access_menu(request.user, "release_track"),
            "can_menu_branch_create": can_access_menu(request.user, "branch_create"),
            "can_menu_release_entry": can_access_menu(request.user, "release_entry"),
            "can_menu_hobo_ledger": can_access_menu(request.user, "hobo_ledger"),
            "can_menu_sql_execute": can_access_menu(request.user, "sql_execute"),
            "can_menu_admin_config": can_access_menu(request.user, "admin_config"),
            "can_manage_role_permissions": request.user.is_superuser,
        },
    )


@login_required
def my_password(request):
    password_form = PasswordChangeForm(user=request.user)
    if request.method == "POST":
        password_form = PasswordChangeForm(user=request.user, data=request.POST)
        if password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "密码修改成功。")
            return redirect("/my-password/")
        messages.error(request, "密码修改失败，请检查输入。")
    return render(
        request,
        "accounts/my_password.html",
        {
            "password_form": password_form,
        },
    )


@login_required
def approval_list(request):
    if not _can_review(request.user):
        messages.error(request, "你没有审核权限。")
        return redirect("/")

    role_key = request.GET.get("role", "").strip()
    start_date_raw = request.GET.get("start_date", "").strip()
    end_date_raw = request.GET.get("end_date", "").strip()
    start_date = parse_date(start_date_raw) if start_date_raw else None
    end_date = parse_date(end_date_raw) if end_date_raw else None

    pending_profiles = (
        UserProfile.objects.select_related("user")
        .filter(approval_status=UserProfile.ApprovalStatus.PENDING)
        .order_by("user__date_joined")
    )
    if not request.user.is_superuser:
        pending_profiles = pending_profiles.filter(role__is_staff_role=False)
    if role_key:
        pending_profiles = pending_profiles.filter(role__key=role_key)
    if start_date:
        pending_profiles = pending_profiles.filter(user__date_joined__date__gte=start_date)
    if end_date:
        pending_profiles = pending_profiles.filter(user__date_joined__date__lte=end_date)

    return render(
        request,
        "accounts/approval_list.html",
        {
            "pending_profiles": pending_profiles,
            "role_choices": RoleDefinition.objects.filter(enabled=True).order_by("id"),
            "filters": {
                "role": role_key,
                "start_date": start_date_raw,
                "end_date": end_date_raw,
            },
        },
    )


@login_required
def approval_action(request, profile_id):
    if request.method != "POST":
        return redirect("/approval/")
    if not _can_review(request.user):
        messages.error(request, "你没有审核权限。")
        return redirect("/")

    target_profile = get_object_or_404(UserProfile.objects.select_related("user"), pk=profile_id)
    if target_profile.approval_status != UserProfile.ApprovalStatus.PENDING:
        messages.error(request, "该账号不是待审核状态。")
        return redirect("/approval/")
    if not _can_review_target(request.user, target_profile):
        messages.error(request, "你无权审核该账号。")
        return redirect("/approval/")

    action = request.POST.get("action", "").strip()
    reason = request.POST.get("reason", "").strip()

    if action == "approve":
        _apply_approval_decision(target_profile, request.user, "approve")
        messages.success(request, f"已通过 {target_profile.user.username} 的账号申请。")
    elif action == "reject":
        _apply_approval_decision(target_profile, request.user, "reject", reason=reason)
        messages.success(request, f"已拒绝 {target_profile.user.username} 的账号申请。")
    else:
        messages.error(request, "无效操作。")

    return redirect("/approval/")


@login_required
def admin_config(request):
    if not _can_manage_admin_config(request.user):
        messages.error(request, "你没有管理员配置权限。")
        return redirect("/")

    password_form = PasswordChangeForm(user=request.user)

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        if action == "change_password":
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "密码修改成功。")
                return redirect("/admin-config/")
            messages.error(request, "密码修改失败，请检查输入。")
        elif action == "save_project":
            project_id = request.POST.get("project_id", "").strip()
            project_code_input = request.POST.get("project_code", "").strip()
            project_name = request.POST.get("project_name", "").strip()
            enabled = request.POST.get("enabled", "").strip() in {"on", "1", "true", "yes"}

            if not project_code_input:
                messages.error(request, "工程编码不能为空。")
                return redirect("/admin-config/")

            try:
                if project_id:
                    project = get_object_or_404(ProjectCatalog, pk=project_id)
                    project.project_code = project_code_input
                    project.project_name = project_name
                    project.enabled = enabled
                    project.save()
                    display_name = project_name or project_code_input
                    messages.success(request, f"工程 {display_name} 更新成功。")
                else:
                    codes = _parse_project_codes(project_code_input)
                    if not codes:
                        messages.error(request, "工程编码不能为空。")
                        return redirect("/admin-config/")

                    created_count = 0
                    skipped_count = 0
                    for code in codes:
                        _, created = ProjectCatalog.objects.get_or_create(
                            project_code=code,
                            defaults={
                                "project_name": project_name,
                                "enabled": enabled,
                            },
                        )
                        if created:
                            created_count += 1
                        else:
                            skipped_count += 1
                    messages.success(
                        request,
                        f"批量新增完成：新增 {created_count} 条，已存在跳过 {skipped_count} 条。",
                    )
            except IntegrityError:
                messages.error(request, "工程编码已存在，请更换后重试。")
            return redirect("/admin-config/")
        elif action == "bulk_save_projects":
            payload = request.POST.get("projects_payload", "").strip()
            if not payload:
                messages.error(request, "缺少工程配置数据。")
                return redirect("/admin-config/")
            try:
                rows = json.loads(payload)
            except json.JSONDecodeError:
                messages.error(request, "工程配置数据格式错误。")
                return redirect("/admin-config/")
            if not isinstance(rows, list):
                messages.error(request, "工程配置数据格式错误。")
                return redirect("/admin-config/")

            # 验证payload中无重复编码
            code_set = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("project_code", "")).strip()
                if not code:
                    continue
                if code in code_set:
                    messages.error(request, f"工程编码重复：{code}")
                    return redirect("/admin-config/")
                code_set.add(code)

            try:
                # 获取数据库中已存在的记录
                existing = {p.project_code: p for p in ProjectCatalog.objects.all()}
                incoming_codes = set(code_set)

                # 需要删除的：数据库有但payload中没有
                to_delete = set(existing.keys()) - incoming_codes
                if to_delete:
                    ProjectCatalog.objects.filter(project_code__in=to_delete).delete()

                # 更新或新增
                to_update = []
                to_create = []
                for row in rows:
                    code = str(row.get("project_code", "")).strip()
                    if not code:
                        continue
                    obj = ProjectCatalog(
                        project_code=code,
                        project_name=str(row.get("project_name", "")).strip(),
                        enabled=bool(row.get("enabled", True)),
                    )
                    if code in existing:
                        obj.id = existing[code].id
                        to_update.append(obj)
                    else:
                        to_create.append(obj)

                updated_count = 0
                created_count = 0
                if to_update:
                    ProjectCatalog.objects.bulk_update(
                        to_update, ["project_name", "enabled"]
                    )
                    updated_count = len(to_update)
                if to_create:
                    ProjectCatalog.objects.bulk_create(to_create)
                    created_count = len(to_create)

                messages.success(
                    request,
                    f"工程配置保存成功：更新 {updated_count} 条，新增 {created_count} 条，删除 {len(to_delete)} 条。",
                )
            except Exception as e:
                messages.error(request, f"保存失败：{e}")
            return redirect("/admin-config/")
        elif action == "save_sql_config":
            git_config = GitPlatformConfig.get_solo_safe()
            if not getattr(git_config, "_db_ready", True):
                messages.error(request, "Git 配置表尚未初始化，请先执行数据库迁移。")
                return redirect("/admin-config/")
            sql_repo_path = request.POST.get("sql_repo_path", "").strip()
            sql_repo_clone_url = request.POST.get("sql_repo_clone_url", "").strip()
            sql_db_host = request.POST.get("sql_db_host", "").strip()
            sql_db_port_raw = request.POST.get("sql_db_port", "").strip()
            sql_db_name = request.POST.get("sql_db_name", "").strip()
            sql_db_user = request.POST.get("sql_db_user", "").strip()
            sql_db_password = request.POST.get("sql_db_password", "")
            sql_keyword_ddl = request.POST.get("sql_keyword_ddl", "").strip()
            sql_keyword_backup = request.POST.get("sql_keyword_backup", "").strip()
            sql_keyword_execute = request.POST.get("sql_keyword_execute", "").strip()
            sql_keyword_rollback = request.POST.get("sql_keyword_rollback", "").strip()
            sql_auto_approve_order = request.POST.get("sql_auto_approve_order", "").strip()
            clear_sql_db_password = request.POST.get("clear_sql_db_password", "").strip() in {
                "on",
                "1",
                "true",
                "yes",
            }
            if sql_db_port_raw and not sql_db_port_raw.isdigit():
                messages.error(request, "SQL 数据库端口必须是数字。")
                return redirect("/admin-config/")

            git_config.sql_repo_path = sql_repo_path
            git_config.sql_repo_clone_url = sql_repo_clone_url
            git_config.sql_db_host = sql_db_host
            git_config.sql_db_port = int(sql_db_port_raw or 3306)
            git_config.sql_db_name = sql_db_name
            git_config.sql_db_user = sql_db_user
            git_config.sql_keyword_ddl = sql_keyword_ddl or "ddl"
            git_config.sql_keyword_backup = sql_keyword_backup or "backup,bak,备份"
            git_config.sql_keyword_execute = sql_keyword_execute or "execute,执行"
            git_config.sql_keyword_rollback = sql_keyword_rollback or "rollback,回滚"
            git_config.sql_auto_approve_order = (
                sql_auto_approve_order or "backup,ddl,execute,rollback"
            )
            git_config.updated_by = request.user

            if clear_sql_db_password:
                git_config.sql_db_password = ""
            elif sql_db_password:
                git_config.sql_db_password = sql_db_password.strip()

            try:
                git_config.save()
            except (ProgrammingError, OperationalError):
                messages.error(request, "SQL 配置保存失败：数据库未完成迁移。")
                return redirect("/admin-config/")
            messages.success(request, "SQL 配置保存成功。")
            return redirect("/admin-config/")
        elif action == "save_git_config":
            git_config = GitPlatformConfig.get_solo_safe()
            if not getattr(git_config, "_db_ready", True):
                messages.error(request, "Git 配置表尚未初始化，请先执行数据库迁移。")
                return redirect("/admin-config/")
            git_base_url = _normalize_git_base_url(request.POST.get("git_base_url", ""))
            git_group = request.POST.get("git_group", "").strip()
            work_base_dir = request.POST.get("work_base_dir", "").strip()
            git_username = request.POST.get("git_username", "").strip()
            git_password = request.POST.get("git_password", "")
            git_pat = request.POST.get("git_pat", "")
            clear_git_password = request.POST.get("clear_git_password", "").strip() in {
                "on",
                "1",
                "true",
                "yes",
            }
            clear_git_pat = request.POST.get("clear_git_pat", "").strip() in {
                "on",
                "1",
                "true",
                "yes",
            }

            if not git_base_url:
                messages.error(request, "Git 地址不能为空。")
                return redirect("/admin-config/")
            if not git_group:
                messages.error(request, "Git Group/命名空间不能为空。")
                return redirect("/admin-config/")
            if not work_base_dir:
                messages.error(request, "本地工作目录不能为空。")
                return redirect("/admin-config/")

            git_config.git_base_url = git_base_url
            git_config.git_group = git_group
            git_config.work_base_dir = work_base_dir
            git_config.git_username = git_username
            git_config.updated_by = request.user

            if clear_git_password:
                git_config.git_password = ""
            elif git_password:
                git_config.git_password = git_password.strip()

            if clear_git_pat:
                git_config.git_pat = ""
            elif git_pat:
                git_config.git_pat = git_pat.strip()

            try:
                git_config.save()
            except (ProgrammingError, OperationalError):
                messages.error(request, "Git 配置保存失败：数据库未完成迁移。")
                return redirect("/admin-config/")
            messages.success(
                request,
                "Git 基础配置保存成功。认证优先级：PAT > 用户名/密码。",
            )
            return redirect("/admin-config/")
        elif action == "delete_project":
            project_id = request.POST.get("project_id", "").strip()
            if not project_id:
                messages.error(request, "缺少工程 ID。")
                return redirect("/admin-config/")
            project = get_object_or_404(ProjectCatalog, pk=project_id)
            project_name = project.project_name
            project.delete()
            messages.success(request, f"工程 {project_name} 已删除。")
            return redirect("/admin-config/")
        elif action == "update_user_account":
            target_user_id = request.POST.get("target_user_id", "").strip()
            new_role_id = request.POST.get("new_role_id", "").strip()
            new_password = request.POST.get("new_password", "").strip()
            if not target_user_id.isdigit():
                messages.error(request, "账号参数无效。")
                return redirect("/admin-config/")

            target_user = get_object_or_404(User, pk=int(target_user_id))
            if not _can_manage_target_account(request.user, target_user):
                messages.error(request, "你无权管理该账号。")
                return redirect("/admin-config/")

            updated_fields = []
            if new_password:
                target_user.set_password(new_password)
                updated_fields.append("password")

            if not target_user.is_superuser:
                default_role = RoleDefinition.get_default_role()
                if not default_role:
                    messages.error(request, "未找到可用角色，请先创建角色。")
                    return redirect("/admin-config/")
                profile, _ = UserProfile.objects.get_or_create(
                    user=target_user,
                    defaults={
                        "role": default_role,
                        "approval_status": UserProfile.ApprovalStatus.APPROVED,
                        "approved_by": request.user,
                        "approved_at": timezone.now(),
                    },
                )
                if new_role_id.isdigit():
                    selected_role = RoleDefinition.objects.filter(
                        id=int(new_role_id), enabled=True
                    ).first()
                    if selected_role:
                        profile.role = selected_role
                        if profile.approval_status == UserProfile.ApprovalStatus.APPROVED:
                            profile.approved_by = request.user
                            profile.approved_at = timezone.now()
                    profile.save(update_fields=["role", "approved_by", "approved_at"])
                target_user.is_staff = (
                    profile.role.is_staff_role
                    and profile.approval_status == UserProfile.ApprovalStatus.APPROVED
                )
                updated_fields.append("is_staff")

            if updated_fields:
                target_user.save(update_fields=list(set(updated_fields)))
                messages.success(request, f"账号 {target_user.username} 更新成功。")
            else:
                messages.error(request, "未检测到可更新内容。")
            return redirect("/admin-config/")
        else:
            messages.error(request, "无效操作。")
            return redirect("/admin-config/")

    missing_profile_users = User.objects.filter(is_superuser=False, profile__isnull=True)
    default_role = RoleDefinition.get_default_role()
    for user in missing_profile_users:
        if not default_role:
            continue
        UserProfile.objects.create(
            user=user,
            role=default_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
            approved_by=request.user,
            approved_at=timezone.now(),
        )

    pending_profiles = UserProfile.objects.select_related("user").filter(
        approval_status=UserProfile.ApprovalStatus.PENDING
    )
    if not request.user.is_superuser:
        pending_profiles = pending_profiles.filter(role__is_staff_role=False)
    pending_profiles = pending_profiles.order_by("user__date_joined")

    projects = ProjectCatalog.objects.order_by("project_name", "id")
    git_config = GitPlatformConfig.get_solo_safe()
    if not getattr(git_config, "_db_ready", True):
        messages.warning(request, "Git 配置表尚未初始化，当前展示的是默认值。请执行 migrate 后再保存。")
    managed_users = (
        User.objects.exclude(id=request.user.id)
        .select_related("profile")
        .order_by("username", "id")
    )
    return render(
        request,
        "accounts/admin_config.html",
        {
            "password_form": password_form,
            "pending_profiles": pending_profiles,
            "projects": projects,
            "git_config": git_config,
            "git_password_masked": GitPlatformConfig.mask_secret(git_config.git_password),
            "git_pat_masked": GitPlatformConfig.mask_secret(git_config.git_pat),
            "sql_db_password_masked": GitPlatformConfig.mask_secret(git_config.sql_db_password),
            "managed_users": managed_users,
            "available_roles": RoleDefinition.objects.filter(enabled=True).order_by("id"),
        },
    )


@login_required
def role_permissions_config(request):
    if not _can_manage_admin_config(request.user):
        messages.error(request, "你没有权限配置角色权限。")
        return redirect("/")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "create_role":
            ok, text = _create_role_from_post(request)
            (messages.success if ok else messages.error)(request, text)
            return redirect("/role-permissions/")
        if action == "update_roles":
            ok, text = _update_roles_from_post(request)
            (messages.success if ok else messages.error)(request, text)
            return redirect("/role-permissions/")
        _save_role_policies_from_post(request)
        messages.success(request, "角色权限范围已保存。")
        return redirect("/role-permissions/")

    context = _build_role_policy_context()
    return render(request, "accounts/role_permissions_config.html", context)


@login_required
@require_http_methods(["GET"])
def notification_counts(request):
    """API: 获取未建分支和未审批 SQL 的数量"""
    try:
        from branch_create.models import HoboRequirementLedger, ReleaseItem, ReleaseBatch
        from sql_execute.models import SqlExecutionRequest

        # 未建分支数（Hobo + Release）
        hobo_count = HoboRequirementLedger.objects.filter(branch_created=False).count()
        release_count = ReleaseItem.objects.filter(
            branch_created=False,
        ).count()
        uncreated_branch_count = hobo_count + release_count

        # 未审批 SQL 数
        unapproved_sql_count = SqlExecutionRequest.objects.filter(
            status=SqlExecutionRequest.Status.PENDING
        ).count()

        return JsonResponse({
            "success": True,
            "uncreated_branch_count": uncreated_branch_count,
            "unapproved_sql_count": unapproved_sql_count,
            "total": uncreated_branch_count + unapproved_sql_count,
        })
    except Exception as e:
        import traceback
        return JsonResponse({
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc(),
        }, status=500)


@login_required
@require_http_methods(["GET"])
def list_managed_users(request):
    """API: 分页获取可管理的用户列表，支持搜索"""
    if not _can_manage_admin_config(request.user):
        return JsonResponse({"success": False, "error": "无权限"}, status=403)

    keyword = request.GET.get("keyword", "").strip()
    page = max(1, int(request.GET.get("page", 1)))
    page_size = min(500, max(10, int(request.GET.get("page_size", 20))))

    queryset = User.objects.exclude(id=request.user.id).select_related("profile")
    if keyword:
        queryset = queryset.filter(username__icontains=keyword)
    queryset = queryset.order_by("username", "id")

    total = queryset.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    users = queryset[offset:offset + page_size]

    from .models import RoleDefinition
    available_roles = list(RoleDefinition.objects.filter(enabled=True).order_by("id").values("id", "name"))

    user_list = []
    for u in users:
        profile = getattr(u, "profile", None)
        current_role = None
        if u.is_superuser:
            current_role = {"id": None, "name": "超管"}
        elif profile and profile.role:
            current_role = {"id": profile.role.id, "name": profile.role.name}
        elif profile:
            current_role = {"id": None, "name": "未设置"}
        else:
            current_role = {"id": None, "name": "未设置"}

        user_list.append({
            "id": u.id,
            "username": u.username,
            "email": u.email or "-",
            "is_superuser": u.is_superuser,
            "current_role": current_role,
        })

    return JsonResponse({
        "success": True,
        "users": user_list,
        "available_roles": available_roles,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    })


@login_required
@require_http_methods(["POST"])
def approval_bulk_action(request):
    if not _can_review(request.user):
        messages.error(request, "你没有审核权限。")
        return redirect("/approval/")

    action = request.POST.get("action", "").strip()
    reason = request.POST.get("reason", "").strip()
    raw_ids = request.POST.getlist("profile_ids")
    profile_ids = [int(v) for v in raw_ids if v.isdigit()]

    if action not in {"approve", "reject"}:
        messages.error(request, "无效操作。")
        return redirect("/approval/")
    if not profile_ids:
        messages.error(request, "请先选择要审核的账号。")
        return redirect("/approval/")

    targets = UserProfile.objects.select_related("user").filter(
        id__in=profile_ids, approval_status=UserProfile.ApprovalStatus.PENDING
    )
    approved_count = 0
    rejected_count = 0
    skipped_count = 0

    for target in targets:
        if not _can_review_target(request.user, target):
            skipped_count += 1
            continue

        _apply_approval_decision(target, request.user, action, reason=reason)
        if action == "approve":
            approved_count += 1
        else:
            rejected_count += 1

    if action == "approve":
        messages.success(
            request,
            f"批量通过完成：通过 {approved_count} 条，跳过 {skipped_count} 条。",
        )
    else:
        messages.success(
            request,
            f"批量拒绝完成：拒绝 {rejected_count} 条，跳过 {skipped_count} 条。",
        )
    return redirect("/approval/")
