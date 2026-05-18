from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db import IntegrityError
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from django.utils import timezone

from accounts.permissions import can_access_menu, can_do_action
from branch_create.models import ExportSchedule


def _can_manage_export_schedule(user, export_type: str) -> bool:
    if user.is_superuser:
        return True
    action_map = {
        ExportSchedule.ExportType.HOBO_LEDGER: "auto_export_hobo_ledger",
        ExportSchedule.ExportType.RELEASE_ENTRY: "auto_export_release_entry",
    }
    action_key = action_map.get(export_type)
    return bool(action_key and can_do_action(user, action_key))


@login_required
def export_schedule_page(request):
    if not can_access_menu(request.user, "export_schedule"):
        return HttpResponseForbidden("无权限访问该功能")
    can_manage_hobo = _can_manage_export_schedule(request.user, ExportSchedule.ExportType.HOBO_LEDGER)
    can_manage_release = _can_manage_export_schedule(request.user, ExportSchedule.ExportType.RELEASE_ENTRY)
    return render(
        request,
        "branch_create/export_schedules.html",
        {
            "can_manage_hobo": can_manage_hobo,
            "can_manage_release": can_manage_release,
        },
    )


@login_required
@require_http_methods(["GET"])
def export_schedule_list_api(request):
    export_type = (request.GET.get("export_type") or "").strip()
    if export_type not in {c.value for c in ExportSchedule.ExportType}:
        return JsonResponse({"success": False, "error": "export_type 非法"}, status=400)
    if not _can_manage_export_schedule(request.user, export_type):
        return JsonResponse({"success": False, "error": "无权限查看"}, status=403)

    schedules = ExportSchedule.objects.filter(export_type=export_type).order_by("-updated_at", "-id")
    data = [
        {
            "id": s.id,
            "name": s.name,
            "enabled": s.enabled,
            "cron_expr": s.cron_expr,
            "export_type": s.export_type,
            "last_run_at": timezone.localtime(s.last_run_at).isoformat() if s.last_run_at else "",
            "created_by": s.created_by.username,
        }
        for s in schedules
    ]
    return JsonResponse({"success": True, "schedules": data})


@login_required
@require_http_methods(["POST"])
def export_schedule_save_api(request):
    schedule_id = (request.POST.get("schedule_id") or "").strip()
    name = (request.POST.get("name") or "").strip()
    cron_expr = (request.POST.get("cron_expr") or "").strip()
    export_type = (request.POST.get("export_type") or "").strip()
    enabled = (request.POST.get("enabled") or "1") in {"1", "true", "on", "yes"}

    if not name or not cron_expr:
        return JsonResponse({"success": False, "error": "名称和 cron 表达式必填"}, status=400)
    if export_type not in {c.value for c in ExportSchedule.ExportType}:
        return JsonResponse({"success": False, "error": "export_type 非法"}, status=400)
    if not _can_manage_export_schedule(request.user, export_type):
        return JsonResponse({"success": False, "error": "无权限操作"}, status=403)

    if schedule_id:
        schedule = ExportSchedule.objects.filter(pk=schedule_id).first()
        if not schedule:
            return JsonResponse({"success": False, "error": "计划任务不存在"}, status=404)
    else:
        schedule = ExportSchedule(created_by=request.user)

    schedule.name = name
    schedule.cron_expr = cron_expr
    schedule.export_type = export_type
    schedule.enabled = enabled
    try:
        schedule.save()
    except IntegrityError:
        return JsonResponse({"success": False, "error": "该导出类型下已存在同名任务，请修改名称"}, status=409)
    return JsonResponse({"success": True, "id": schedule.id})


@login_required
@require_http_methods(["POST"])
def export_schedule_delete_api(request):
    schedule_id = (request.POST.get("schedule_id") or "").strip()
    schedule = ExportSchedule.objects.filter(pk=schedule_id).first()
    if not schedule:
        return JsonResponse({"success": False, "error": "计划任务不存在"}, status=404)
    if not _can_manage_export_schedule(request.user, schedule.export_type):
        return JsonResponse({"success": False, "error": "无权限操作"}, status=403)
    schedule.delete()
    return JsonResponse({"success": True})


@login_required
@require_http_methods(["POST"])
def export_schedule_run_now_api(request):
    schedule_id = (request.POST.get("schedule_id") or "").strip()
    schedule = ExportSchedule.objects.filter(pk=schedule_id).first()
    if not schedule:
        return JsonResponse({"success": False, "error": "计划任务不存在"}, status=404)
    if not _can_manage_export_schedule(request.user, schedule.export_type):
        return JsonResponse({"success": False, "error": "无权限操作"}, status=403)

    try:
        call_command("run_export_schedules", schedule_id=schedule.id)
    except Exception as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=500)

    return JsonResponse({"success": True, "message": "导出任务已完成"})
