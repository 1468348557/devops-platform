import io
import re
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse, JsonResponse
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
            "can_bulk_update_ops_fields": _can_bulk_update_ops_fields(request.user),
            "can_export_release_entry": can_do_action(request.user, "release_entry_export"),
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


def _tri_state_sheet_bool(value) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未填写"


def _release_entry_list_queryset(request):
    """与列表 API 相同的筛选与数据范围，供列表与导出共用。"""
    batch_id = request.GET.get("batch_id")
    if not batch_id:
        return "batch_id 必填", None, None

    today = timezone.localdate()
    default_start = today - timedelta(days=30)
    start_date = parse_date((request.GET.get("start_date") or "").strip()) or default_start
    end_date = parse_date((request.GET.get("end_date") or "").strip()) or today
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    flow_name_kw = (request.GET.get("flow_name") or "").strip()
    applicant_kw = (request.GET.get("applicant_name") or "").strip()
    project_id = (request.GET.get("project_id") or "").strip()

    items = (
        ReleaseItem.objects.select_related("project", "developer", "batch")
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
    if applicant_kw:
        items = items.filter(
            Q(developer__username__icontains=applicant_kw)
            | Q(developer__first_name__icontains=applicant_kw)
            | Q(developer__last_name__icontains=applicant_kw)
        )
    if project_id:
        items = items.filter(project_id=project_id)

    filters_meta = {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "flow_name": flow_name_kw,
        "applicant_name": applicant_kw,
        "project_id": project_id,
    }
    return None, items, filters_meta


def _release_entry_xls_bytes(batch: ReleaseBatch, items: list[ReleaseItem]) -> bytes:
    import xlwt

    headers = [
        "批次投产日期",
        "批次投产分支",
        "工程编码",
        "工程名称",
        "分支类型",
        "需求分支",
        "仅SQL上线",
        "流程/功能名称",
        "业务种类",
        "行项投产分支",
        "科技联系人",
        "业务联系人",
        "公共组件分支",
        "需要参数投产",
        "参数已确认",
        "需要菜单",
        "菜单已新增",
        "需要DIFS",
        "需要流程图",
        "流程图已核对",
        "流程定义名称",
        "实施单元编号",
        "备注",
        "需要事件平台",
        "需要任务池",
        "需要BPMP",
        "需要镜像",
        "需要ESF",
        "需要交易申调",
        "需要投产验证",
        "需要配置文件投产",
        "REL测试状态",
        "REL是否已部署",
        "投产状态",
        "行状态",
        "分支是否已创建",
        "创建失败原因",
        "填写人",
    ]

    book = xlwt.Workbook(encoding="utf-8")
    sheet = book.add_sheet("投产征集")

    for col, title in enumerate(headers):
        sheet.write(0, col, title)

    for row_idx, item in enumerate(items, start=1):
        req_branch = ""
        if item.sql_only_release:
            req_branch = "仅SQL上线（无需需求分支）"
        elif item.requirement_branch:
            req_branch = item.requirement_branch

        values = [
            str(batch.release_date),
            batch.release_branch,
            item.project.project_code,
            item.project.project_name,
            item.get_branch_type_display(),
            req_branch,
            "是" if item.sql_only_release else "否",
            item.flow_name,
            item.biz_category,
            item.release_branch,
            item.tech_owner,
            item.biz_owner,
            item.common_component_branch,
            _tri_state_sheet_bool(item.need_param_release),
            _tri_state_sheet_bool(item.param_confirmed),
            _tri_state_sheet_bool(item.need_menu),
            _tri_state_sheet_bool(item.menu_added),
            _tri_state_sheet_bool(item.need_difs),
            _tri_state_sheet_bool(item.need_flowchart),
            _tri_state_sheet_bool(item.flowchart_checked),
            item.flow_definition_name,
            item.implementation_unit_no,
            item.remark,
            _tri_state_sheet_bool(item.need_event_platform),
            _tri_state_sheet_bool(item.need_task_pool),
            _tri_state_sheet_bool(item.need_bpmp),
            _tri_state_sheet_bool(item.need_image),
            _tri_state_sheet_bool(item.need_esf),
            _tri_state_sheet_bool(item.need_trade_tuning),
            _tri_state_sheet_bool(item.need_release_verify),
            _tri_state_sheet_bool(item.need_config_release),
            (item.rel_test_status or "").strip(),
            (
                ""
                if item.rel_deployed is None
                else ("是" if item.rel_deployed else "否")
            ),
            (item.deploy_status or "").strip(),
            item.get_line_status_display(),
            "已创建" if item.branch_created else "未创建",
            item.branch_create_error or "",
            item.developer.username,
        ]
        for col_idx, cell in enumerate(values):
            sheet.write(row_idx, col_idx, cell)

    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def _can_bulk_update_ops_fields(user) -> bool:
    if user.is_superuser:
        return True
    editable_fields = _get_release_entry_editable_fields(user)
    if not (editable_fields & RELEASE_ENTRY_OPS_FIELD_KEYS):
        return False
    return can_do_action(user, "release_item_edit_others")


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
        "sql_only_release": item.sql_only_release,
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
        "need_config_release": item.need_config_release,
        "common_component_branch": item.common_component_branch,
        "rel_deployed": item.rel_deployed,
        "deploy_status": item.deploy_status,
        "rel_test_status": item.rel_test_status,
        "branch_created": item.branch_created,
        "branch_created_at": item.branch_created_at.isoformat() if item.branch_created_at else "",
        "branch_create_error": item.branch_create_error,
        "developer": item.developer.username,
        "editable": editable,
        "can_delete": is_superuser or current_user_id == item.developer_id,
        "can_edit_dev_fields": can_edit_dev_fields,
        "can_edit_ops_fields": can_edit_ops_fields,
        "missing_fields": missing_fields,
        "incomplete_count": len(missing_fields),
    }


# 「引用上次填写」API 返回字段：与 _item_to_dict 对齐，避免与列表接口漂移或遗漏键。
_QUOTE_LAST_ITEM_KEYS = (
    "flow_name",
    "biz_category",
    "tech_owner",
    "biz_owner",
    "common_component_branch",
    "flow_definition_name",
    "implementation_unit_no",
    "remark",
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
    "need_config_release",
    "rel_test_status",
)


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
            "need_config_release",
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


def _save_item_or_error(item: ReleaseItem):
    try:
        item.save()
        return None
    except IntegrityError as exc:
        detail = str(exc)
        lowered = detail.lower()
        is_requirement_branch_duplicate = (
            ("requirement_branch" in lowered and "unique" in lowered)
            or ("duplicate entry" in lowered and "requirement_branch" in lowered)
            or ("unique constraint failed" in lowered and "requirement_branch" in lowered)
        )
        if is_requirement_branch_duplicate:
            dup_match = re.search(r"Duplicate entry '([^']+)'", detail, flags=re.IGNORECASE)
            dup_value = dup_match.group(1).strip() if dup_match else ""
            if dup_value:
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"需求分支已存在：{dup_value}，请更换后重试",
                    },
                    status=400,
                )
            return JsonResponse(
                {"success": False, "error": "需求分支已存在，请更换后重试"},
                status=400,
            )
        return JsonResponse({"success": False, "error": f"保存失败：{detail}"}, status=400)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"success": False, "error": f"保存失败：{str(exc)}"}, status=500)


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
    requirement_branch_raw = request.POST.get("requirement_branch", "").strip()
    requirement_branch = requirement_branch_raw or None
    sql_only_release = False
    if not requirement_branch:
        sql_only_release = True

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
        sql_only_release=sql_only_release,
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
    save_error = _save_item_or_error(item)
    if save_error is not None:
        return save_error
    return JsonResponse({"success": True, "item": _item_to_dict(item, request.user)})


@login_required
@require_http_methods(["GET"])
def release_entry_item_list(request):
    err, items, filters_meta = _release_entry_list_queryset(request)
    if err:
        return JsonResponse({"success": False, "error": err}, status=400)

    data = [_item_to_dict(item, request.user) for item in items]
    return JsonResponse({"success": True, "items": data, "filters": filters_meta})


@login_required
@require_http_methods(["GET"])
def release_entry_export_xls(request):
    if not can_do_action(request.user, "release_entry_export"):
        return HttpResponse("无导出权限", status=403, content_type="text/plain; charset=utf-8")

    err, items_qs, _filters_meta = _release_entry_list_queryset(request)
    if err:
        return HttpResponse(err, status=400, content_type="text/plain; charset=utf-8")

    items = list(items_qs)
    batch_id = request.GET.get("batch_id")
    try:
        batch = ReleaseBatch.objects.get(pk=int(batch_id))
    except (ValueError, ReleaseBatch.DoesNotExist):
        return HttpResponse("批次不存在", status=404, content_type="text/plain; charset=utf-8")

    raw_name = f"release_entry_{batch.release_date}_{batch.release_branch}.xls"
    safe_name = re.sub(r"[^\w.\-]+", "_", raw_name)
    if not safe_name.lower().endswith(".xls"):
        safe_name = f"{safe_name}.xls"

    payload = _release_entry_xls_bytes(batch, items)
    response = HttpResponse(payload, content_type="application/vnd.ms-excel")
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return response


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

    quote_payload = {
        k: v
        for k, v in _item_to_dict(last_item, request.user).items()
        if k in _QUOTE_LAST_ITEM_KEYS
    }
    return JsonResponse(
        {
            "success": True,
            "item": quote_payload,
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
        item.requirement_branch = requirement_branch or None
        item.sql_only_release = not bool(requirement_branch)

    _apply_item_fields(
        item,
        request,
        editable_fields=editable_fields,
        allow_dev_scope=can_edit_dev_scope,
        allow_ops_scope=can_edit_ops_scope,
    )
    save_error = _save_item_or_error(item)
    if save_error is not None:
        return save_error
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
def release_entry_item_bulk_update(request):
    if not _can_bulk_update_ops_fields(request.user):
        return JsonResponse({"success": False, "error": "仅运维或超管可批量修改"}, status=403)

    batch_id = (request.POST.get("batch_id") or "").strip()
    raw_item_ids = (request.POST.get("item_ids") or "").strip()
    field_name = (request.POST.get("field_name") or "").strip()
    field_value = request.POST.get("field_value")
    if field_value is None:
        field_value = ""

    if not batch_id or not batch_id.isdigit():
        return JsonResponse({"success": False, "error": "batch_id 非法"}, status=400)
    if not raw_item_ids:
        return JsonResponse({"success": False, "error": "请选择至少一条记录"}, status=400)

    if field_name not in {"deploy_status", "rel_deployed"}:
        return JsonResponse({"success": False, "error": "仅支持批量修改运维字段"}, status=400)

    try:
        item_ids = [int(part) for part in raw_item_ids.split(",") if part.strip()]
    except ValueError:
        return JsonResponse({"success": False, "error": "item_ids 格式非法"}, status=400)
    if not item_ids:
        return JsonResponse({"success": False, "error": "请选择至少一条记录"}, status=400)

    editable_fields = _get_release_entry_editable_fields(request.user)
    if (not request.user.is_superuser) and (field_name not in editable_fields):
        return JsonResponse({"success": False, "error": "当前角色无该字段批量修改权限"}, status=403)

    items = list(ReleaseItem.objects.filter(batch_id=int(batch_id), id__in=item_ids).select_related("batch"))
    if not items:
        return JsonResponse({"success": False, "error": "未找到可更新记录"}, status=404)

    blocked = [str(item.id) for item in items if item.batch.status != ReleaseBatch.Status.OPEN]
    if blocked:
        return JsonResponse(
            {"success": False, "error": f"以下记录所在批次未开放，不能修改：{', '.join(blocked)}"},
            status=400,
        )

    if field_name == "deploy_status":
        normalized = str(field_value).strip()
        if normalized not in {"", "是", "否"}:
            return JsonResponse({"success": False, "error": "投产状态仅支持：是/否/未填写"}, status=400)
        for item in items:
            item.deploy_status = normalized
    else:
        raw = str(field_value).strip()
        if raw not in {"", "是", "否", "true", "false", "1", "0"}:
            return JsonResponse({"success": False, "error": "REL是否已部署仅支持：是/否/未填写"}, status=400)
        parsed = _parse_bool(field_value)
        for item in items:
            item.rel_deployed = parsed

    ReleaseItem.objects.bulk_update(items, [field_name, "updated_at"])
    return JsonResponse({"success": True, "updated_count": len(items)})


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
    try:
        batch.delete()
    except ProtectedError:
        return JsonResponse(
            {
                "success": False,
                "error": "当前批次存在受保护关联数据，无法删除。请先处理相关记录后再删除。",
            },
            status=400,
        )
    except IntegrityError:
        return JsonResponse(
            {
                "success": False,
                "error": "当前批次存在数据库约束关联，暂时无法删除。",
            },
            status=400,
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse(
            {
                "success": False,
                "error": f"删除批次失败：{exc}",
            },
            status=500,
        )
    return JsonResponse(
        {
            "success": True,
            "message": f"已删除批次 {release_date} / {release_branch}",
        }
    )
