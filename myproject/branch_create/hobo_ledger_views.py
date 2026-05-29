import io
import re
from typing import Optional
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from accounts.permissions import apply_data_scope, can_access_menu, can_do_action
from .models import HoboRequirementLedger, ProjectCatalog, ReleaseItem

BRANCH_SUFFIX_REGEX = re.compile(r"^[\w-]{1,50}$")


@login_required
def hobo_ledger_page(request):
    if not _can_use_ledger(request.user):
        return HttpResponseForbidden("无权限访问该功能")
    profile = getattr(request.user, "profile", None)
    role = profile.role.key if profile and profile.role_id else ""
    return render(
        request,
        "branch_create/hobo_requirement_ledger.html",
        {
            "current_role": role,
            "can_create_dev_record": can_do_action(request.user, "hobo_item_create"),
            "can_export_hobo_ledger": can_do_action(request.user, "hobo_ledger_export"),
        },
    )


def _can_use_ledger(user):
    return can_access_menu(user, "hobo_ledger")


def _can_create_or_edit(user, entry: Optional[HoboRequirementLedger]):
    if user.is_superuser:
        return True
    if entry is None:
        return can_do_action(user, "hobo_item_create")
    if can_do_action(user, "hobo_item_edit_others"):
        return True
    if not can_do_action(user, "hobo_item_edit_own"):
        return False
    return entry.created_by_id == user.id


def _resolve_applicant_name(user, posted: str) -> str:
    cleaned = (posted or "").strip()
    if cleaned:
        return cleaned
    full = user.get_full_name().strip()
    return full or user.username


def _item_to_dict(entry: HoboRequirementLedger, user) -> dict:
    is_superuser = user.is_superuser
    is_owner = entry.created_by_id == user.id
    can_edit = is_superuser or can_do_action(user, "hobo_item_edit_others") or is_owner
    return {
        "id": entry.id,
        "requirement_type": entry.requirement_type,
        "requirement_branch": entry.requirement_branch,
        "project": {
            "id": entry.project_id,
            "code": entry.project.project_code,
            "name": entry.project.project_name or entry.project.project_code,
        },
        "description": entry.description,
        "applicant_name": entry.applicant_name,
        "applied_date": str(entry.applied_date),
        "base_branch": entry.base_branch,
        "base_branch_contact": entry.base_branch_contact,
        "flowchart_name": entry.flowchart_name,
        "uat_submit_date": str(entry.uat_submit_date) if entry.uat_submit_date else "",
        "rel_submit_date": str(entry.rel_submit_date) if entry.rel_submit_date else "",
        "production_date": str(entry.production_date) if entry.production_date else "",
        "remark": entry.remark,
        "branch_created": entry.branch_created,
        "branch_created_at": timezone.localtime(entry.branch_created_at).isoformat() if entry.branch_created_at else "",
        "branch_create_error": entry.branch_create_error,
        "created_by": entry.created_by.username,
        "editable": can_edit,
        "can_delete": can_edit,
    }


def _hobo_dependency_fields_error(base_branch: str, base_branch_contact: str) -> str | None:
    bb = (base_branch or "").strip()
    cc = (base_branch_contact or "").strip()
    if bb and not cc:
        return "已填写依赖分支时，必须填写依赖分支联系人"
    return None


def _parse_optional_date(raw: Optional[str]):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return parse_date(s)


def _hobo_ledger_xlsx_bytes(items: list[HoboRequirementLedger]) -> bytes:
    from openpyxl import Workbook

    headers = [
        "需求类型",
        "分支名称",
        "工程编码",
        "工程名称",
        "需求描述",
        "申请人",
        "申请日期",
        "依赖分支",
        "依赖分支联系人",
        "流程图名称",
        "提交 UAT 日期",
        "提交 REL 日期",
        "投产日期",
        "备注",
        "是否已建分支",
        "建分支时间",
        "建分支操作人",
        "创建失败原因",
        "登记人账号",
    ]

    wb = Workbook()
    sheet = wb.active
    sheet.title = "HOBO需求登记"
    sheet.append(headers)

    for entry in items:
        proj_name = entry.project.project_name or entry.project.project_code
        branch_by = ""
        if entry.branch_created_by_id:
            branch_by = entry.branch_created_by.username
        sheet.append([
            entry.requirement_type,
            entry.requirement_branch,
            entry.project.project_code,
            proj_name,
            entry.description,
            entry.applicant_name,
            str(entry.applied_date),
            entry.base_branch,
            entry.base_branch_contact,
            entry.flowchart_name,
            str(entry.uat_submit_date) if entry.uat_submit_date else "",
            str(entry.rel_submit_date) if entry.rel_submit_date else "",
            str(entry.production_date) if entry.production_date else "",
            entry.remark,
            "是" if entry.branch_created else "否",
            timezone.localtime(entry.branch_created_at).strftime("%Y-%m-%d %H:%M:%S") if entry.branch_created_at else "",
            branch_by,
            entry.branch_create_error or "",
            entry.created_by.username,
        ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse_branch_suffix(request):
    enabled = (request.POST.get("custom_branch_suffix_enabled") or "").lower() in {
        "1",
        "true",
        "on",
        "yes",
    }
    suffix = (request.POST.get("custom_branch_suffix") or "").strip()
    if not enabled:
        return "", None
    if not suffix:
        return "", "请填写额外分支名称"
    if len(suffix) > 50:
        return "", "额外分支名称最多 50 个字"
    if not BRANCH_SUFFIX_REGEX.match(suffix):
        return "", "额外分支名称仅支持中文、字母、数字、下划线和中划线"
    return suffix, None


@login_required
@require_http_methods(["GET"])
def hobo_ledger_project_list(request):
    if not _can_use_ledger(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)
    rows = (
        ProjectCatalog.objects.filter(enabled=True)
        .order_by("project_name", "id")
        .values("id", "project_code", "project_name")
    )
    projects = [
        {
            "id": r["id"],
            "project_code": r["project_code"],
            "project_name": r["project_name"] or r["project_code"],
        }
        for r in rows
    ]
    return JsonResponse({"success": True, "projects": projects})


@login_required
@require_http_methods(["GET"])
def hobo_ledger_item_list(request):
    if not _can_use_ledger(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)

    today = timezone.localdate()
    default_start = today - timedelta(days=30)
    start_date = parse_date((request.GET.get("start_date") or "").strip()) or default_start
    end_date = parse_date((request.GET.get("end_date") or "").strip()) or today
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    description_kw = (request.GET.get("description") or "").strip()
    applicant_kw = (request.GET.get("applicant_name") or "").strip()
    requirement_type = (request.GET.get("requirement_type") or "").strip().upper()
    project_id = (request.GET.get("project_id") or "").strip()

    items = (
        HoboRequirementLedger.objects.active().select_related("project", "created_by")
        .filter(applied_date__gte=start_date, applied_date__lte=end_date)
        .order_by("-applied_date", "-id")
    )
    items = apply_data_scope(
        items,
        request.user,
        scope_key="hobo_ledger",
        owner_field="created_by",
    )
    if description_kw:
        items = items.filter(description__icontains=description_kw)
    if applicant_kw:
        items = items.filter(applicant_name__icontains=applicant_kw)
    if requirement_type in {c.value for c in HoboRequirementLedger.BranchPrefix}:
        items = items.filter(requirement_type=requirement_type)
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
                "description": description_kw,
                "applicant_name": applicant_kw,
                "requirement_type": requirement_type,
                "project_id": project_id,
            },
        }
    )


@login_required
@require_http_methods(["GET"])
def hobo_ledger_export_xlsx(request):
    if not _can_use_ledger(request.user):
        return HttpResponse("无权限访问", status=403, content_type="text/plain; charset=utf-8")
    if not can_do_action(request.user, "hobo_ledger_export"):
        return HttpResponse("无导出权限", status=403, content_type="text/plain; charset=utf-8")

    items_qs = (
        HoboRequirementLedger.objects.active().select_related(
            "project", "created_by", "branch_created_by"
        ).order_by("-applied_date", "-id")
    )
    items_qs = apply_data_scope(
        items_qs,
        request.user,
        scope_key="hobo_ledger",
        owner_field="created_by",
    )
    items = list(items_qs)

    raw_name = f"hobo_requirement_ledger_{timezone.localdate().isoformat()}.xlsx"
    safe_name = re.sub(r"[^\w.\-]+", "_", raw_name)
    if not safe_name.lower().endswith(".xlsx"):
        safe_name = f"{safe_name}.xlsx"

    payload = _hobo_ledger_xlsx_bytes(items)
    response = HttpResponse(
        payload,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return response


@login_required
@require_http_methods(["POST"])
def hobo_ledger_item_create(request):
    if not _can_use_ledger(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)
    if not _can_create_or_edit(request.user, None):
        return JsonResponse({"success": False, "error": "仅研发或超管可新增登记"}, status=403)

    requirement_type = (request.POST.get("requirement_type") or "").strip().upper()
    project_id = request.POST.get("project_id")
    description = (request.POST.get("description") or "").strip()
    applicant_raw = request.POST.get("applicant_name")

    valid_prefixes = {c.value for c in HoboRequirementLedger.BranchPrefix}
    if requirement_type not in valid_prefixes:
        return JsonResponse(
            {"success": False, "error": "需求类型须为 FIX / REQ / PUB"}, status=400
        )
    if not project_id:
        return JsonResponse({"success": False, "error": "工程名称必填"}, status=400)
    if not description:
        return JsonResponse({"success": False, "error": "需求描述必填"}, status=400)

    try:
        project = ProjectCatalog.objects.get(pk=project_id, enabled=True)
    except ProjectCatalog.DoesNotExist:
        return JsonResponse({"success": False, "error": "工程不存在或已禁用"}, status=400)

    branch_suffix, branch_suffix_error = _parse_branch_suffix(request)
    if branch_suffix_error:
        return JsonResponse({"success": False, "error": branch_suffix_error}, status=400)

    base_branch = (request.POST.get("base_branch") or "").strip()
    base_branch_contact = (request.POST.get("base_branch_contact") or "").strip()
    dep_err = _hobo_dependency_fields_error(base_branch, base_branch_contact)
    if dep_err:
        return JsonResponse({"success": False, "error": dep_err}, status=400)

    requirement_branch = ReleaseItem._next_requirement_branch(requirement_type)
    if branch_suffix:
        requirement_branch = f"{requirement_branch}-{branch_suffix}"
    entry = HoboRequirementLedger(
        requirement_type=requirement_type,
        requirement_branch=requirement_branch,
        project=project,
        description=description,
        applicant_name=_resolve_applicant_name(request.user, applicant_raw or ""),
        applied_date=timezone.localdate(),
        base_branch=base_branch,
        base_branch_contact=base_branch_contact,
        flowchart_name=(request.POST.get("flowchart_name") or "").strip(),
        uat_submit_date=_parse_optional_date(request.POST.get("uat_submit_date")),
        rel_submit_date=_parse_optional_date(request.POST.get("rel_submit_date")),
        production_date=_parse_optional_date(request.POST.get("production_date")),
        remark=(request.POST.get("remark") or "").strip(),
        created_by=request.user,
    )
    entry.save()
    return JsonResponse({"success": True, "item": _item_to_dict(entry, request.user)})


@login_required
@require_http_methods(["POST"])
def hobo_ledger_item_update(request):
    item_id = request.POST.get("item_id")
    if not item_id:
        return JsonResponse({"success": False, "error": "item_id 必填"}, status=400)

    if not _can_use_ledger(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)

    try:
        entry = HoboRequirementLedger.objects.active().select_related("project", "created_by").get(pk=item_id)
    except HoboRequirementLedger.DoesNotExist:
        return JsonResponse({"success": False, "error": "记录不存在"}, status=404)

    if not _can_create_or_edit(request.user, entry):
        return JsonResponse({"success": False, "error": "你没有修改该记录的权限"}, status=403)

    project_id = request.POST.get("project_id")
    if project_id:
        try:
            entry.project = ProjectCatalog.objects.get(pk=project_id, enabled=True)
        except ProjectCatalog.DoesNotExist:
            return JsonResponse({"success": False, "error": "工程不存在或已禁用"}, status=400)

    description = request.POST.get("description")
    if description is not None:
        description = description.strip()
        if not description:
            return JsonResponse({"success": False, "error": "需求描述不能为空"}, status=400)
        entry.description = description

    applicant_name = request.POST.get("applicant_name")
    if applicant_name is not None:
        a = applicant_name.strip()
        if a:
            entry.applicant_name = a

    base_branch = request.POST.get("base_branch")
    if base_branch is not None:
        entry.base_branch = base_branch.strip()

    for field, post_key in (
        ("base_branch_contact", "base_branch_contact"),
        ("flowchart_name", "flowchart_name"),
        ("remark", "remark"),
    ):
        val = request.POST.get(post_key)
        if val is not None:
            setattr(entry, field, val.strip())

    for field, post_key in (
        ("uat_submit_date", "uat_submit_date"),
        ("rel_submit_date", "rel_submit_date"),
        ("production_date", "production_date"),
    ):
        if request.POST.get(post_key) is not None:
            setattr(entry, field, _parse_optional_date(request.POST.get(post_key)))

    dep_err = _hobo_dependency_fields_error(entry.base_branch, entry.base_branch_contact)
    if dep_err:
        return JsonResponse({"success": False, "error": dep_err}, status=400)

    entry.save()
    return JsonResponse({"success": True, "item": _item_to_dict(entry, request.user)})


@login_required
@require_http_methods(["POST"])
def hobo_ledger_item_delete(request):
    item_id = request.POST.get("item_id")
    if not item_id:
        return JsonResponse({"success": False, "error": "item_id 必填"}, status=400)

    if not _can_use_ledger(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)

    try:
        entry = HoboRequirementLedger.objects.active().get(pk=item_id)
    except HoboRequirementLedger.DoesNotExist:
        return JsonResponse({"success": False, "error": "记录不存在"}, status=404)

    if not can_do_action(request.user, "hobo_item_delete_own"):
        return JsonResponse({"success": False, "error": "无删除权限"}, status=403)
    if not request.user.is_superuser and entry.created_by_id != request.user.id:
        return JsonResponse({"success": False, "error": "仅本人或超管可删除"}, status=403)

    entry.is_deleted = True
    entry.save(update_fields=["is_deleted", "updated_at"])
    return JsonResponse({"success": True})
