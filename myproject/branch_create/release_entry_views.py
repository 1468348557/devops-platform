import re
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from accounts.models import RolePermissionPolicy
from accounts.permissions import apply_data_scope, can_do_action
from accounts.role_meta import (
    RELEASE_ENTRY_DEV_FIELD_KEYS,
    RELEASE_ENTRY_FIELD_OPTIONS,
    RELEASE_ENTRY_OPS_FIELD_KEYS,
)
from .models import (
    ProjectCatalog,
    ReleaseBatch,
    ReleaseBatchProject,
    ReleaseBranchSequence,
    ReleaseItem,
    BRANCH_REGEX,
)


@login_required
def release_entry_page(request):
    profile = getattr(request.user, "profile", None)
    role = profile.role.key if profile and profile.role_id else ""
    editable_fields = _get_release_entry_editable_fields(request.user)
    can_edit_ops_fields = bool(editable_fields & RELEASE_ENTRY_OPS_FIELD_KEYS)
    can_manage_batch = can_do_action(request.user, "release_batch_manage")
    return render(
        request,
        "branch_create/release_entry.html",
        {
            "is_staff_user": request.user.is_staff,
            "is_superuser_user": request.user.is_superuser,
            "current_role": role,
            "can_edit_ops_fields": can_edit_ops_fields,
            "can_manage_batch": can_manage_batch,
            "can_create_dev_record": can_do_action(request.user, "release_item_create"),
        },
    )


def _admin_required_json(request):
    if can_do_action(request.user, "release_batch_manage"):
        return None
    return JsonResponse({"success": False, "error": "仅管理员可操作"}, status=403)


def _parse_bool(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "是"}:
        return True
    if normalized in {"0", "false", "no", "n", "否"}:
        return False
    return None


def _get_release_entry_editable_fields(user) -> set[str]:
    if user.is_superuser:
        return {key for key, _ in RELEASE_ENTRY_FIELD_OPTIONS}
    profile = getattr(user, "profile", None)
    if not profile or not profile.role_id:
        return set()
    policy = RolePermissionPolicy.get_for_role(profile.role)
    if not policy:
        return set()
    valid_keys = {key for key, _ in RELEASE_ENTRY_FIELD_OPTIONS}
    return {
        field_key
        for field_key in (policy.release_entry_editable_fields or [])
        if field_key in valid_keys
    }


DEFAULT_BATCH_PROJECTS = [
    ("hobo-customer-front", "客户流程前端"),
    ("hobo-credit-front", "授信前端"),
    ("hobo-asset-front", "资产前端"),
    ("hobo-payment-front", "支付前端"),
    ("hobo-deposit-front", "存款前端"),
    ("hobo-work-front", "工作台前端"),
]


def _get_default_projects():
    configured = list(
        ProjectCatalog.objects.filter(enabled=True)
        .order_by("project_name")
        .values_list("project_code", "project_name")
    )
    if configured:
        return [(code, name or code) for code, name in configured]
    return DEFAULT_BATCH_PROJECTS


def _sync_batch_projects_with_catalog(batch: ReleaseBatch) -> None:
    """将批次工程枚举与管理员配置页保持同步。"""
    catalog_map = {
        code: {"name": name, "enabled": enabled}
        for code, name, enabled in ProjectCatalog.objects.values_list(
            "project_code", "project_name", "enabled"
        )
    }
    if not catalog_map:
        catalog_map = {
            code: {"name": name, "enabled": True}
            for code, name in DEFAULT_BATCH_PROJECTS
        }

    existing_by_code = {p.project_code: p for p in batch.projects.all()}

    for code, meta in catalog_map.items():
        existing = existing_by_code.get(code)
        if existing:
            new_name = meta["name"] or code
            if existing.project_name != new_name or existing.enabled != meta["enabled"]:
                existing.project_name = new_name
                existing.enabled = meta["enabled"]
                existing.save(update_fields=["project_name", "enabled"])
        else:
            ReleaseBatchProject.objects.create(
                batch=batch,
                project_code=code,
                project_name=meta["name"] or code,
                enabled=meta["enabled"],
            )

    # 配置中已删除的工程，在批次中自动置为禁用，避免旧数据误选。
    for code, existing in existing_by_code.items():
        if code not in catalog_map and existing.enabled:
            existing.enabled = False
            existing.save(update_fields=["enabled"])


def _build_release_branch(release_type: str, release_date):
    return f"{release_type}-{release_date.strftime('%Y%m%d')}"


def _item_to_dict(item: ReleaseItem, user) -> dict:
    current_user_id = user.id
    is_superuser = user.is_superuser
    is_owner = current_user_id == item.developer_id
    editable_fields = _get_release_entry_editable_fields(user)
    can_edit_base_scope = is_superuser or is_owner or can_do_action(user, "release_item_edit_others")
    can_edit_dev_scope = can_edit_base_scope
    can_edit_ops_scope = can_edit_base_scope
    can_edit_dev_fields = can_edit_dev_scope and bool(editable_fields & RELEASE_ENTRY_DEV_FIELD_KEYS)
    can_edit_ops_fields = can_edit_ops_scope and bool(editable_fields & RELEASE_ENTRY_OPS_FIELD_KEYS)
    editable = can_edit_dev_fields or can_edit_ops_fields

    missing_fields = item.get_missing_fields()
    return {
        "id": item.id,
        "batch_id": item.batch_id,
        "project": {
            "id": item.project_id,
            "code": item.project.project_code,
            "name": item.project.project_name,
        },
        "flow_name": item.flow_name,
        "biz_category": item.biz_category,
        "branch_type": item.branch_type,
        "requirement_branch": item.requirement_branch,
        "release_branch": item.release_branch,
        "tech_owner": item.tech_owner,
        "biz_owner": item.biz_owner,
        "line_status": item.line_status,
        "need_param_release": item.need_param_release,
        "param_confirmed": item.param_confirmed,
        "need_menu": item.need_menu,
        "menu_added": item.menu_added,
        "need_difs": item.need_difs,
        "need_flowchart": item.need_flowchart,
        "flowchart_checked": item.flowchart_checked,
        "flow_definition_name": item.flow_definition_name,
        "implementation_unit_no": item.implementation_unit_no,
        "remark": item.remark,
        "need_event_platform": item.need_event_platform,
        "need_task_pool": item.need_task_pool,
        "need_bpmp": item.need_bpmp,
        "need_image": item.need_image,
        "need_esf": item.need_esf,
        "need_trade_tuning": item.need_trade_tuning,
        "need_release_verify": item.need_release_verify,
        "common_component_branch": item.common_component_branch,
        "rel_deployed": item.rel_deployed,
        "deploy_status": item.deploy_status,
        "rel_test_status": item.rel_test_status,
        "branch_created": item.branch_created,
        "branch_created_at": item.branch_created_at.isoformat() if item.branch_created_at else "",
        "branch_create_error": item.branch_create_error,
        "developer": item.developer.username,
        "editable": editable,
        "can_delete": current_user_id == item.developer_id,
        "can_edit_dev_fields": can_edit_dev_fields,
        "can_edit_ops_fields": can_edit_ops_fields,
        "missing_fields": missing_fields,
        "incomplete_count": len(missing_fields),
    }


def _apply_item_fields(
    item: ReleaseItem,
    request,
    editable_fields: set[str],
    allow_dev_scope: bool,
    allow_ops_scope: bool,
):
    if allow_dev_scope:
        for field in ("flow_name", "biz_category", "tech_owner", "biz_owner", "common_component_branch"):
            if field not in editable_fields:
                continue
            value = request.POST.get(field)
            if value is not None:
                setattr(item, field, value.strip())

        requirement_branch = request.POST.get("requirement_branch")
        if requirement_branch is not None and "requirement_branch" in editable_fields:
            item.requirement_branch = requirement_branch.strip()

        release_branch = request.POST.get("release_branch")
        if release_branch is not None and "release_branch" in editable_fields:
            item.release_branch = release_branch.strip()

        bool_fields = (
            "need_param_release",
            "param_confirmed",
            "need_menu",
            "menu_added",
            "need_difs",
            "need_flowchart",
            "flowchart_checked",
            "need_event_platform",
            "need_task_pool",
            "need_bpmp",
            "need_image",
            "need_esf",
            "need_trade_tuning",
            "need_release_verify",
        )
        for field in bool_fields:
            if field not in editable_fields:
                continue
            value = request.POST.get(field)
            if value is not None:
                setattr(item, field, _parse_bool(value))

        flow_definition_name = request.POST.get("flow_definition_name")
        if flow_definition_name is not None and "flow_definition_name" in editable_fields:
            item.flow_definition_name = flow_definition_name.strip()

        implementation_unit_no = request.POST.get("implementation_unit_no")
        if implementation_unit_no is not None and "implementation_unit_no" in editable_fields:
            item.implementation_unit_no = implementation_unit_no.strip()

        remark = request.POST.get("remark")
        if remark is not None and "remark" in editable_fields:
            item.remark = remark.strip()

        rel_test_status = request.POST.get("rel_test_status")
        if rel_test_status is not None and "rel_test_status" in editable_fields:
            item.rel_test_status = rel_test_status.strip()

    if allow_ops_scope:
        rel_deployed = request.POST.get("rel_deployed")
        if rel_deployed is not None and "rel_deployed" in editable_fields:
            item.rel_deployed = _parse_bool(rel_deployed)
        deploy_status = request.POST.get("deploy_status")
        if deploy_status is not None and "deploy_status" in editable_fields:
            item.deploy_status = deploy_status.strip()


@login_required
@require_http_methods(["GET"])
def release_entry_batch_list(request):
    batches = (
        ReleaseBatch.objects.select_related("created_by")
        .prefetch_related("projects")
        .order_by("-release_date")
    )
    data = []
    for batch in batches:
        _sync_batch_projects_with_catalog(batch)
        batch.refresh_from_db()
        batch_projects = batch.projects.order_by("project_name", "id")
        data.append(
            {
                "id": batch.id,
                "release_date": str(batch.release_date),
                "release_type": batch.release_type,
                "release_branch": batch.release_branch,
                "status": batch.status,
                "created_by": batch.created_by.username,
                "projects": [
                    {
                        "id": p.id,
                        "project_code": p.project_code,
                        "project_name": p.project_name,
                        "enabled": p.enabled,
                    }
                    for p in batch_projects
                ],
            }
        )
    return JsonResponse({"success": True, "batches": data})


@login_required
@require_http_methods(["POST"])
def release_entry_item_create(request):
    if not (request.user.is_superuser or can_do_action(request.user, "release_item_create")):
        return JsonResponse({"success": False, "error": "仅研发或超管可新增记录"}, status=403)

    batch_id = request.POST.get("batch_id")
    project_id = request.POST.get("project_id")
    flow_name = request.POST.get("flow_name", "").strip()
    biz_category = request.POST.get("biz_category", "").strip()
    tech_owner = request.POST.get("tech_owner", "").strip()
    biz_owner = request.POST.get("biz_owner", "").strip()
    branch_type = request.POST.get(
        "branch_type", ReleaseBranchSequence.BranchType.REQ
    ).strip()

    if not batch_id or not project_id:
        return JsonResponse({"success": False, "error": "batch_id 和 project_id 必填"}, status=400)
    requirement_branch = request.POST.get("requirement_branch", "").strip()
    if not requirement_branch:
        return JsonResponse({"success": False, "error": "需求分支必填"}, status=400)
    if not re.match(BRANCH_REGEX, requirement_branch):
        return JsonResponse(
            {
                "success": False,
                "error": "需求分支格式错误，应为 FIX/REQ/PUB-yyyymmdd-xxxx",
            },
            status=400,
        )

    try:
        batch = ReleaseBatch.objects.get(pk=batch_id)
        project = ReleaseBatchProject.objects.get(pk=project_id, batch=batch, enabled=True)
    except (ReleaseBatch.DoesNotExist, ReleaseBatchProject.DoesNotExist):
        return JsonResponse({"success": False, "error": "批次或工程不存在"}, status=404)

    if batch.status != ReleaseBatch.Status.OPEN:
        return JsonResponse({"success": False, "error": "当前批次未开放填写"}, status=400)

    valid_branch_types = {choice[0] for choice in ReleaseBranchSequence.BranchType.choices}
    if branch_type not in valid_branch_types:
        return JsonResponse({"success": False, "error": "branch_type 非法"}, status=400)

    item = ReleaseItem(
        batch=batch,
        project=project,
        flow_name=flow_name,
        biz_category=biz_category,
        branch_type=branch_type,
        requirement_branch=requirement_branch,
        release_branch=batch.release_branch,
        tech_owner=tech_owner,
        biz_owner=biz_owner,
        developer=request.user,
    )
    editable_fields = _get_release_entry_editable_fields(request.user)
    _apply_item_fields(
        item,
        request,
        editable_fields=editable_fields,
        allow_dev_scope=True,
        allow_ops_scope=request.user.is_superuser,
    )
    item.save()
    return JsonResponse({"success": True, "item": _item_to_dict(item, request.user)})


@login_required
@require_http_methods(["GET"])
def release_entry_item_list(request):
    batch_id = request.GET.get("batch_id")
    if not batch_id:
        return JsonResponse({"success": False, "error": "batch_id 必填"}, status=400)

    today = timezone.localdate()
    default_start = today - timedelta(days=30)
    start_date = parse_date((request.GET.get("start_date") or "").strip()) or default_start
    end_date = parse_date((request.GET.get("end_date") or "").strip()) or today
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    flow_name_kw = (request.GET.get("flow_name") or "").strip()
    project_id = (request.GET.get("project_id") or "").strip()

    items = (
        ReleaseItem.objects.select_related("project", "developer")
        .filter(batch_id=batch_id)
        .filter(created_at__date__gte=start_date, created_at__date__lte=end_date)
        .order_by("-updated_at", "-id")
    )
    items = apply_data_scope(
        items,
        request.user,
        scope_key="release_entry",
        owner_field="developer",
    )
    if flow_name_kw:
        items = items.filter(flow_name__icontains=flow_name_kw)
    if project_id:
        items = items.filter(project_id=project_id)

    data = [_item_to_dict(item, request.user) for item in items]
    return JsonResponse(
        {
            "success": True,
            "items": data,
            "filters": {
                "start_date": str(start_date),
                "end_date": str(end_date),
                "flow_name": flow_name_kw,
                "project_id": project_id,
            },
        }
    )


@login_required
@require_http_methods(["GET"])
def release_entry_item_last_by_project(request):
    project_id = (request.GET.get("project_id") or "").strip()
    if not project_id:
        return JsonResponse({"success": False, "error": "project_id 必填"}, status=400)
    batch_id = (request.GET.get("batch_id") or "").strip()

    try:
        project = ReleaseBatchProject.objects.get(pk=project_id)
    except ReleaseBatchProject.DoesNotExist:
        return JsonResponse({"success": False, "error": "工程不存在"}, status=404)

    exclude_item_id = (request.GET.get("exclude_item_id") or "").strip()
    items = ReleaseItem.objects.select_related("project", "batch").filter(
        project__project_code=project.project_code
    )
    if exclude_item_id.isdigit():
        items = items.exclude(pk=int(exclude_item_id))

    # 优先引用“上一个批次”的同工程内容，再回退到更早历史。
    if batch_id.isdigit():
        prev_batch_items = items.exclude(batch_id=int(batch_id)).order_by(
            "-batch__release_date", "-updated_at", "-id"
        )
        last_item = prev_batch_items.first()
    else:
        last_item = None
    if not last_item:
        last_item = items.order_by("-batch__release_date", "-updated_at", "-id").first()
    if not last_item:
        return JsonResponse({"success": True, "item": None})

    return JsonResponse(
        {
            "success": True,
            "item": {
                "flow_name": last_item.flow_name,
                "biz_category": last_item.biz_category,
                "tech_owner": last_item.tech_owner,
                "biz_owner": last_item.biz_owner,
                "common_component_branch": last_item.common_component_branch,
                "flow_definition_name": last_item.flow_definition_name,
                "implementation_unit_no": last_item.implementation_unit_no,
                "remark": last_item.remark,
                "need_param_release": last_item.need_param_release,
                "param_confirmed": last_item.param_confirmed,
                "need_menu": last_item.need_menu,
                "menu_added": last_item.menu_added,
                "need_difs": last_item.need_difs,
                "need_flowchart": last_item.need_flowchart,
                "flowchart_checked": last_item.flowchart_checked,
                "need_event_platform": last_item.need_event_platform,
                "need_task_pool": last_item.need_task_pool,
                "need_bpmp": last_item.need_bpmp,
                "need_image": last_item.need_image,
                "need_esf": last_item.need_esf,
                "need_trade_tuning": last_item.need_trade_tuning,
                "need_release_verify": last_item.need_release_verify,
                "rel_test_status": last_item.rel_test_status,
            },
            "source": {
                "item_id": last_item.id,
                "project_code": last_item.project.project_code,
                "project_name": last_item.project.project_name,
                "batch_id": last_item.batch_id,
                "batch_release_date": str(last_item.batch.release_date),
                "batch_release_branch": last_item.batch.release_branch,
            },
        }
    )


@login_required
@require_http_methods(["POST"])
def release_entry_item_update(request):
    item_id = request.POST.get("item_id")
    if not item_id:
        return JsonResponse({"success": False, "error": "item_id 必填"}, status=400)

    try:
        item = ReleaseItem.objects.select_related("developer", "batch").get(pk=item_id)
    except ReleaseItem.DoesNotExist:
        return JsonResponse({"success": False, "error": "记录不存在"}, status=404)

    is_superuser = request.user.is_superuser
    is_owner = item.developer_id == request.user.id
    editable_fields = _get_release_entry_editable_fields(request.user)
    can_edit_base_scope = is_superuser or is_owner or can_do_action(
        request.user, "release_item_edit_others"
    )
    can_edit_dev_scope = can_edit_base_scope
    can_edit_ops_scope = can_edit_base_scope
    can_edit_dev_fields = can_edit_dev_scope and bool(editable_fields & RELEASE_ENTRY_DEV_FIELD_KEYS)
    can_edit_ops_fields = can_edit_ops_scope and bool(editable_fields & RELEASE_ENTRY_OPS_FIELD_KEYS)
    if not can_edit_base_scope:
        return JsonResponse({"success": False, "error": "你没有修改该记录的权限"}, status=403)

    if item.batch.status != ReleaseBatch.Status.OPEN:
        return JsonResponse({"success": False, "error": "当前批次未开放填写"}, status=400)

    project_id = request.POST.get("project_id")
    if project_id and can_edit_dev_scope and "project_id" in editable_fields:
        try:
            item.project = ReleaseBatchProject.objects.get(
                pk=project_id, batch=item.batch, enabled=True
            )
        except ReleaseBatchProject.DoesNotExist:
            return JsonResponse({"success": False, "error": "工程不存在或已禁用"}, status=400)

    branch_type = request.POST.get("branch_type")
    if branch_type and can_edit_dev_scope and "branch_type" in editable_fields:
        valid_branch_types = {choice[0] for choice in ReleaseBranchSequence.BranchType.choices}
        if branch_type not in valid_branch_types:
            return JsonResponse({"success": False, "error": "branch_type 非法"}, status=400)
        item.branch_type = branch_type

    requirement_branch = request.POST.get("requirement_branch")
    if requirement_branch is not None and can_edit_dev_scope and "requirement_branch" in editable_fields:
        requirement_branch = requirement_branch.strip()
        if not requirement_branch:
            return JsonResponse({"success": False, "error": "需求分支不能为空"}, status=400)
        if not re.match(BRANCH_REGEX, requirement_branch):
            return JsonResponse(
                {
                    "success": False,
                    "error": "需求分支格式错误，应为 FIX/REQ/PUB-yyyymmdd-xxxx",
                },
                status=400,
            )

    _apply_item_fields(
        item,
        request,
        editable_fields=editable_fields,
        allow_dev_scope=can_edit_dev_scope,
        allow_ops_scope=can_edit_ops_scope,
    )
    item.save()
    return JsonResponse({"success": True, "item": _item_to_dict(item, request.user)})


@login_required
@require_http_methods(["POST"])
def release_entry_item_submit(request):
    item_id = request.POST.get("item_id")
    if not item_id:
        return JsonResponse({"success": False, "error": "item_id 必填"}, status=400)

    try:
        item = ReleaseItem.objects.select_related("batch").get(pk=item_id)
    except ReleaseItem.DoesNotExist:
        return JsonResponse({"success": False, "error": "记录不存在"}, status=404)

    is_superuser = request.user.is_superuser
    can_edit_others = can_do_action(request.user, "release_item_edit_others")
    if not is_superuser and not (item.developer_id == request.user.id or can_edit_others):
        return JsonResponse({"success": False, "error": "只能提交自己的记录"}, status=403)

    missing = item.get_missing_fields()
    if missing:
        item.line_status = ReleaseItem.LineStatus.INCOMPLETE
        item.save(update_fields=["line_status", "updated_at"])
        return JsonResponse(
            {"success": False, "error": "仍有未填写内容", "missing_fields": missing}, status=400
        )

    item.line_status = ReleaseItem.LineStatus.SUBMITTED
    item.save(update_fields=["line_status", "updated_at"])
    return JsonResponse({"success": True, "item": _item_to_dict(item, request.user)})


@login_required
@require_http_methods(["POST"])
def release_entry_item_delete(request):
    item_id = request.POST.get("item_id")
    if not item_id:
        return JsonResponse({"success": False, "error": "item_id 必填"}, status=400)

    try:
        item = ReleaseItem.objects.select_related("batch").get(pk=item_id)
    except ReleaseItem.DoesNotExist:
        return JsonResponse({"success": False, "error": "记录不存在"}, status=404)

    if not can_do_action(request.user, "release_item_delete_own"):
        return JsonResponse({"success": False, "error": "无删除权限"}, status=403)
    if not request.user.is_superuser and item.developer_id != request.user.id:
        return JsonResponse({"success": False, "error": "仅创建人可删除该记录"}, status=403)
    if item.batch.status != ReleaseBatch.Status.OPEN:
        return JsonResponse({"success": False, "error": "当前批次未开放，不能删除"}, status=400)

    item.delete()
    return JsonResponse({"success": True})


@login_required
@require_http_methods(["POST"])
def release_entry_batch_create(request):
    admin_check = _admin_required_json(request)
    if admin_check:
        return admin_check

    release_date_raw = request.POST.get("release_date", "").strip()
    release_type = request.POST.get(
        "release_type", ReleaseBatch.ReleaseType.RELEASE
    ).strip()
    projects_text = request.POST.get("projects", "").strip()

    release_date = parse_date(release_date_raw)
    if not release_date:
        return JsonResponse({"success": False, "error": "release_date 格式无效"}, status=400)
    if release_type not in {
        ReleaseBatch.ReleaseType.RELEASE,
        ReleaseBatch.ReleaseType.HOTFIX,
    }:
        return JsonResponse({"success": False, "error": "release_type 非法"}, status=400)

    release_branch = _build_release_branch(release_type, release_date)

    try:
        batch = ReleaseBatch.objects.create(
            release_date=release_date,
            release_type=release_type,
            release_branch=release_branch,
            status=ReleaseBatch.Status.OPEN,
            created_by=request.user,
        )
    except IntegrityError:
        return JsonResponse({"success": False, "error": "该投产日期批次已存在"}, status=400)

    if projects_text:
        lines = [line.strip() for line in projects_text.splitlines() if line.strip()]
    else:
        lines = [f"{code},{name}" for code, name in _get_default_projects()]

    for raw in lines:
        if "," in raw:
            project_code, project_name = [v.strip() for v in raw.split(",", 1)]
        else:
            project_code, project_name = raw, raw
        ReleaseBatchProject.objects.create(
            batch=batch,
            project_code=project_code,
            project_name=project_name,
        )

    return JsonResponse(
        {
            "success": True,
            "batch_id": batch.id,
            "release_branch": release_branch,
        }
    )


@login_required
@require_http_methods(["POST"])
def release_entry_batch_delete(request):
    admin_check = _admin_required_json(request)
    if admin_check:
        return admin_check

    batch_id = request.POST.get("batch_id", "").strip()
    if not batch_id:
        return JsonResponse({"success": False, "error": "batch_id 必填"}, status=400)
    try:
        batch = ReleaseBatch.objects.get(pk=batch_id)
    except ReleaseBatch.DoesNotExist:
        return JsonResponse({"success": False, "error": "批次不存在"}, status=404)

    release_date = str(batch.release_date)
    release_branch = batch.release_branch
    batch.delete()
    return JsonResponse(
        {
            "success": True,
            "message": f"已删除批次 {release_date} / {release_branch}",
        }
    )
