from typing import Optional
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from accounts.permissions import apply_data_scope, can_access_menu, can_do_action
from .models import HoboRequirementLedger, ProjectCatalog, ReleaseItem


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
        "branch_created_at": entry.branch_created_at.isoformat() if entry.branch_created_at else "",
        "branch_create_error": entry.branch_create_error,
        "created_by": entry.created_by.username,
        "editable": can_edit,
        "can_delete": can_edit,
    }


def _parse_optional_date(raw: Optional[str]):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return parse_date(s)


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
    requirement_type = (request.GET.get("requirement_type") or "").strip().upper()
    project_id = (request.GET.get("project_id") or "").strip()

    items = (
        HoboRequirementLedger.objects.select_related("project", "created_by")
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
                "requirement_type": requirement_type,
                "project_id": project_id,
            },
        }
    )


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

    base_branch = (request.POST.get("base_branch") or "").strip() or "master"
    requirement_branch = ReleaseItem._next_requirement_branch(requirement_type)
    entry = HoboRequirementLedger(
        requirement_type=requirement_type,
        requirement_branch=requirement_branch,
        project=project,
        description=description,
        applicant_name=_resolve_applicant_name(request.user, applicant_raw or ""),
        applied_date=timezone.localdate(),
        base_branch=base_branch,
        base_branch_contact=(request.POST.get("base_branch_contact") or "").strip(),
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
        entry = HoboRequirementLedger.objects.select_related("project", "created_by").get(pk=item_id)
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
        entry.base_branch = base_branch.strip() or "master"

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
        entry = HoboRequirementLedger.objects.get(pk=item_id)
    except HoboRequirementLedger.DoesNotExist:
        return JsonResponse({"success": False, "error": "记录不存在"}, status=404)

    if not can_do_action(request.user, "hobo_item_delete_own"):
        return JsonResponse({"success": False, "error": "无删除权限"}, status=403)
    if not request.user.is_superuser and entry.created_by_id != request.user.id:
        return JsonResponse({"success": False, "error": "仅本人或超管可删除"}, status=403)

    entry.delete()
    return JsonResponse({"success": True})
