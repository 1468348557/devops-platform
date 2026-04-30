"""
新建分支 - 视图
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from accounts.permissions import can_access_menu, can_do_action
from .config_parser import parse_branch_config
from .models import (
    BranchCreateSchedule,
    BranchTaskExecuteRun,
    BranchTaskExecuteRunItem,
    ReleaseBatch,
    ReleaseBatchProject,
)
from .services.branch_executor import BranchExecutor, BranchTaskInput
from .services.branch_tasks import (
    TaskQueryFilters,
    collect_pending_tasks,
    execute_tasks,
    filter_preview_tasks_with_remote_check,
    run_schedule,
)


def _cron_matches(expr: str, now) -> bool:
    parts = str(expr or "").split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts

    def match(token: str, value: int) -> bool:
        if token == "*":
            return True
        if token.startswith("*/"):
            try:
                step = int(token[2:])
                return step > 0 and value % step == 0
            except ValueError:
                return False
        if "," in token:
            return any(match(t.strip(), value) for t in token.split(","))
        try:
            return int(token) == value
        except ValueError:
            return False

    return (
        match(minute, now.minute)
        and match(hour, now.hour)
        and match(dom, now.day)
        and match(month, now.month)
        and match(dow, now.weekday())
    )


def _create_job_payload_file(prefix: str, payload: dict) -> Path:
    jobs_dir = Path(settings.BASE_DIR) / ".runtime" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    payload_file = jobs_dir / f"{prefix}-{uuid.uuid4().hex}.json"
    payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload_file


def _spawn_detached_command(*args: str) -> None:
    subprocess.Popen(  # noqa: S603
        [sys.executable, "manage.py", *args],
        cwd=str(settings.BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

def _can_access_branch_create(user):
    return can_access_menu(user, "branch_create")


def _can_execute_hobo(user):
    return can_do_action(user, "branch_task_execute_hobo")


def _can_execute_release(user):
    return can_do_action(user, "branch_task_execute_release")


def _can_view_hobo(user):
    return can_do_action(user, "branch_task_preview")


def _can_view_release(user):
    return can_do_action(user, "branch_task_preview")


def _normalize_execute_payload(request):
    payload_tasks = []
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
            payload_tasks = body.get("tasks") or []
        except json.JSONDecodeError:
            return None, JsonResponse({"success": False, "error": "JSON 格式错误"}, status=400)
    else:
        try:
            payload_tasks = json.loads(request.POST.get("tasks", "[]"))
        except json.JSONDecodeError:
            return None, JsonResponse({"success": False, "error": "tasks 格式错误"}, status=400)

    if not isinstance(payload_tasks, list) or not payload_tasks:
        return None, JsonResponse({"success": False, "error": "未选择任务"}, status=400)

    normalized = []
    for t in payload_tasks:
        if not isinstance(t, dict):
            continue
        source_type = str(t.get("source_type", "")).strip().lower()
        if source_type == "hobo" and not _can_execute_hobo(request.user):
            return None, JsonResponse({"success": False, "error": "无需求分支执行权限"}, status=403)
        if source_type == "release" and not _can_execute_release(request.user):
            return None, JsonResponse({"success": False, "error": "无投产分支执行权限"}, status=403)
        normalized.append(
            {
                "source_type": source_type,
                "source_id": int(t.get("source_id")),
                "project_code": str(t.get("project_code", "")).strip(),
                "new_branch": str(t.get("new_branch", "")).strip(),
                "base_branch": str(t.get("base_branch", "master")).strip() or "master",
            }
        )
    if not normalized:
        return None, JsonResponse({"success": False, "error": "未找到有效任务"}, status=400)
    return normalized, None


def _run_execute_job(run_id: str, task_refs: list[dict], operator_id: int) -> None:
    User = get_user_model()
    operator = User.objects.filter(pk=operator_id).first()
    run = BranchTaskExecuteRun.objects.filter(run_id=run_id).first()
    if not run:
        return
    if not operator:
        run.status = BranchTaskExecuteRun.Status.FAILED
        run.error = "执行人不存在"
        run.tip = "执行失败：执行人不存在"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "tip", "finished_at", "updated_at"])
        return

    def on_progress(processed: int, total: int, row: dict) -> None:
        BranchTaskExecuteRunItem.objects.create(
            run=run,
            seq=processed,
            source_type=row.get("source_type", ""),
            source_id=int(row.get("source_id") or 0),
            project_code=row.get("project_code", ""),
            new_branch=row.get("new_branch", ""),
            status=row.get("status", "failed"),
            message=(row.get("message", "") or "")[:255],
            log=(row.get("log", "") or "")[:20000],
        )
        run.processed_count = processed
        run.success_count = BranchTaskExecuteRunItem.objects.filter(
            run=run, status="success"
        ).count()
        run.skipped_count = BranchTaskExecuteRunItem.objects.filter(
            run=run, status="skipped"
        ).count()
        run.failed_count = BranchTaskExecuteRunItem.objects.filter(
            run=run, status="failed"
        ).count()
        run.tip = f"执行中 {processed}/{total}"
        run.save(
            update_fields=[
                "processed_count",
                "success_count",
                "skipped_count",
                "failed_count",
                "tip",
                "updated_at",
            ]
        )

    try:
        summary = execute_tasks(task_refs, operator, progress_callback=on_progress)
        run.status = BranchTaskExecuteRun.Status.SUCCESS
        run.total_count = summary["total"]
        run.processed_count = summary["total"]
        run.success_count = summary["success"]
        run.skipped_count = summary["skipped"]
        run.failed_count = summary["failed"]
        run.tip = (
            f"执行完成：成功 {summary['success']}，"
            f"跳过 {summary['skipped']}，失败 {summary['failed']}"
        )
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "total_count",
                "processed_count",
                "success_count",
                "skipped_count",
                "failed_count",
                "tip",
                "finished_at",
                "updated_at",
            ]
        )
    except Exception as exc:  # noqa: BLE001
        run.status = BranchTaskExecuteRun.Status.FAILED
        run.error = str(exc)[:255]
        run.tip = f"执行失败：{exc}"
        run.finished_at = timezone.now()
        run.save(
            update_fields=["status", "error", "tip", "finished_at", "updated_at"]
        )


def _serialize_execute_run(run: BranchTaskExecuteRun) -> dict:
    rows = [
        {
            "source_type": item.source_type,
            "source_id": item.source_id,
            "project_code": item.project_code,
            "new_branch": item.new_branch,
            "status": item.status,
            "message": item.message,
            "log": item.log,
        }
        for item in run.items.all()
    ]
    return {
        "status": run.status,
        "total": run.total_count,
        "processed": run.processed_count,
        "success": run.success_count,
        "skipped": run.skipped_count,
        "failed": run.failed_count,
        "rows": rows,
        "tip": run.tip,
        "error": run.error,
    }


def admin_required(view_func):
    return user_passes_test(
        lambda u: u.is_authenticated
        and (
            can_do_action(u, "branch_task_execute_release")
            or can_do_action(u, "branch_task_execute_hobo")
        )
    )(view_func)


@login_required
def branch_create_index(request):
    if not _can_access_branch_create(request.user):
        messages.error(request, "当前账号无新建分支权限。")
        return redirect("/")

    open_release_projects = (
        ReleaseBatchProject.objects.filter(batch__status=ReleaseBatch.Status.OPEN, enabled=True)
        .select_related("batch")
        .order_by("project_name", "id")
    )
    release_project_options = [
        {
            "id": p.id,
            "project_code": p.project_code,
            "project_name": p.project_name,
            "batch_id": p.batch_id,
            "batch_release_branch": p.batch.release_branch,
        }
        for p in open_release_projects
    ]

    schedules = (
        BranchCreateSchedule.objects.select_related("created_by").order_by("-updated_at", "-id")
        if can_do_action(request.user, "schedule_manage")
        else []
    )
    return render(
        request,
        "branch_create/index.html",
        {
            "can_view_hobo": _can_view_hobo(request.user),
            "can_view_release": _can_view_release(request.user),
            "can_execute_tasks": _can_execute_hobo(request.user) or _can_execute_release(request.user),
            "release_project_options": release_project_options,
            "schedules": schedules,
            "can_manage_schedule": can_do_action(request.user, "schedule_manage"),
        },
    )


@login_required
@admin_required
def branch_create_execute(request):
    if request.method == "POST":
        config_text = request.POST.get("config_text", "").strip()
        base_branch = request.POST.get("base_branch", "master").strip()

        if not config_text:
            messages.error(request, "配置内容不能为空")
            return redirect("branch_create:index")

        tasks = parse_branch_config(config_text, base_branch)

        return render(
            request,
            "branch_create/result.html",
            {
                "tasks": tasks,
                "base_branch": base_branch,
            },
        )

    return redirect("branch_create:index")


@login_required
@admin_required
@require_http_methods(["POST"])
def branch_create_api_precheck(request):
    config_text = request.POST.get("config_text", "").strip()
    base_branch = request.POST.get("base_branch", "master").strip()

    if not config_text:
        return JsonResponse({"success": False, "error": "配置内容为空"})

    try:
        tasks = parse_branch_config(config_text, base_branch)
        return JsonResponse(
            {
                "success": True,
                "tasks": [
                    {
                        "line_no": t.line_no,
                        "new_branch": t.new_branch,
                        "raw_project": t.raw_project,
                        "mapped_project": t.mapped_project,
                    }
                    for t in tasks
                ],
                "base_branch": base_branch,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"success": False, "error": str(exc)})


@login_required
@admin_required
@require_http_methods(["POST"])
def branch_create_api_create(request):
    new_branch = request.POST.get("new_branch", "").strip()
    project = request.POST.get("project", "").strip()
    base_branch = request.POST.get("base_branch", "master").strip()
    if not new_branch or not project:
        return JsonResponse({"success": False, "error": "参数不完整"})

    result = BranchExecutor().execute(
        BranchTaskInput(
            source_type="manual",
            source_id=0,
            project_code=project,
            new_branch=new_branch,
            base_branch=base_branch,
        )
    )
    return JsonResponse(
        {
            "success": result.status == "success",
            "status": result.status,
            "project": result.project_code,
            "new_branch": result.new_branch,
            "error": "" if result.status == "success" else result.message,
        }
    )


@login_required
@require_http_methods(["POST"])
def branch_task_preview_api(request):
    if not _can_access_branch_create(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)

    source_type = (request.POST.get("source_type") or "both").strip().lower()
    if source_type in {"release", "both"} and not _can_view_release(request.user):
        return JsonResponse({"success": False, "error": "无投产分支查看权限"}, status=403)
    if source_type in {"hobo", "both"} and not _can_view_hobo(request.user):
        return JsonResponse({"success": False, "error": "无需求分支查看权限"}, status=403)

    filters = TaskQueryFilters(
        start_date=request.POST.get("start_date", ""),
        end_date=request.POST.get("end_date", ""),
        days_back=int(request.POST.get("days_back", "30") or 30),
        hobo_description=request.POST.get("hobo_description", ""),
        hobo_requirement_type=(request.POST.get("hobo_requirement_type", "") or "").upper(),
        hobo_project_id=request.POST.get("hobo_project_id", ""),
        release_flow_name=request.POST.get("release_flow_name", ""),
        release_project_id=request.POST.get("release_project_id", ""),
        include_created=str(request.POST.get("include_created", "")).strip().lower() in {"1", "true", "yes", "on"},
    )
    try:
        tasks = collect_pending_tasks(source_type, filters)
        if filters.include_created:
            auto_marked_count = 0
        else:
            tasks, auto_marked_count = filter_preview_tasks_with_remote_check(
                tasks,
                request.user,
                keep_auto_marked=False,
            )
        return JsonResponse(
            {
                "success": True,
                "tasks": tasks,
                "auto_marked_count": auto_marked_count,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@login_required
@require_http_methods(["POST"])
def branch_task_execute_api(request):
    if not _can_access_branch_create(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)

    normalized, error_resp = _normalize_execute_payload(request)
    if error_resp:
        return error_resp

    summary = execute_tasks(normalized, request.user)
    return JsonResponse({"success": True, **summary})


@login_required
@require_http_methods(["POST"])
def branch_task_execute_start_api(request):
    if not _can_access_branch_create(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)

    normalized, error_resp = _normalize_execute_payload(request)
    if error_resp:
        return error_resp

    run_id = uuid.uuid4().hex
    BranchTaskExecuteRun.objects.create(
        run_id=run_id,
        status=BranchTaskExecuteRun.Status.RUNNING,
        triggered_by=request.user,
        total_count=len(normalized),
        processed_count=0,
        success_count=0,
        skipped_count=0,
        failed_count=0,
        tip=f"执行中 0/{len(normalized)}",
    )
    payload_file = _create_job_payload_file(
        "branch-execute",
        {"task_refs": normalized, "operator_id": request.user.id},
    )
    _spawn_detached_command(
        "run_branch_execute_run",
        run_id,
        str(payload_file),
    )
    return JsonResponse({"success": True, "run_id": run_id})


@login_required
@require_http_methods(["GET"])
def branch_task_execute_progress_api(request):
    if not _can_access_branch_create(request.user):
        return JsonResponse({"success": False, "error": "无权限访问"}, status=403)
    run_id = (request.GET.get("run_id") or "").strip()
    if not run_id:
        return JsonResponse({"success": False, "error": "缺少 run_id"}, status=400)
    run = (
        BranchTaskExecuteRun.objects.select_related("triggered_by")
        .prefetch_related("items")
        .filter(run_id=run_id)
        .first()
    )
    if not run:
        return JsonResponse({"success": False, "error": "执行任务不存在或已过期"}, status=404)
    if run.triggered_by_id and run.triggered_by_id != request.user.id and not request.user.is_superuser:
        return JsonResponse({"success": False, "error": "无权限查看该执行记录"}, status=403)
    return JsonResponse({"success": True, "run": _serialize_execute_run(run)})


@login_required
@require_http_methods(["GET"])
def schedule_list_api(request):
    if not can_do_action(request.user, "schedule_manage"):
        return JsonResponse({"success": False, "error": "仅管理员可查看"}, status=403)
    schedules = BranchCreateSchedule.objects.order_by("-updated_at", "-id")
    data = [
        {
            "id": s.id,
            "name": s.name,
            "enabled": s.enabled,
            "cron_expr": s.cron_expr,
            "source_type": s.source_type,
            "days_back": s.days_back,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else "",
            "created_by": s.created_by.username,
        }
        for s in schedules
    ]
    return JsonResponse({"success": True, "schedules": data})


@login_required
@require_http_methods(["POST"])
def schedule_save_api(request):
    if not can_do_action(request.user, "schedule_manage"):
        return JsonResponse({"success": False, "error": "仅管理员可操作"}, status=403)

    schedule_id = (request.POST.get("schedule_id") or "").strip()
    name = (request.POST.get("name") or "").strip()
    cron_expr = (request.POST.get("cron_expr") or "").strip()
    source_type = (request.POST.get("source_type") or "both").strip().lower()
    days_back = int(request.POST.get("days_back") or 30)
    enabled = (request.POST.get("enabled") or "1") in {"1", "true", "on", "yes"}

    if not name or not cron_expr:
        return JsonResponse({"success": False, "error": "名称和 cron 表达式必填"}, status=400)
    if source_type not in {c.value for c in BranchCreateSchedule.SourceType}:
        return JsonResponse({"success": False, "error": "source_type 非法"}, status=400)

    if schedule_id:
        schedule = BranchCreateSchedule.objects.filter(pk=schedule_id).first()
        if not schedule:
            return JsonResponse({"success": False, "error": "计划任务不存在"}, status=404)
    else:
        schedule = BranchCreateSchedule(created_by=request.user)

    schedule.name = name
    schedule.cron_expr = cron_expr
    schedule.source_type = source_type
    schedule.days_back = days_back
    schedule.enabled = enabled
    schedule.save()
    return JsonResponse({"success": True, "id": schedule.id})


@login_required
@require_http_methods(["POST"])
def schedule_delete_api(request):
    if not can_do_action(request.user, "schedule_manage"):
        return JsonResponse({"success": False, "error": "仅管理员可操作"}, status=403)
    schedule_id = (request.POST.get("schedule_id") or "").strip()
    schedule = BranchCreateSchedule.objects.filter(pk=schedule_id).first()
    if not schedule:
        return JsonResponse({"success": False, "error": "计划任务不存在"}, status=404)
    schedule.delete()
    return JsonResponse({"success": True})


@login_required
@require_http_methods(["POST"])
def schedule_run_api(request):
    if not can_do_action(request.user, "schedule_manage"):
        return JsonResponse({"success": False, "error": "仅管理员可操作"}, status=403)
    schedule_id = (request.POST.get("schedule_id") or "").strip()
    schedule = BranchCreateSchedule.objects.filter(pk=schedule_id).first()
    if not schedule:
        return JsonResponse({"success": False, "error": "计划任务不存在"}, status=404)
    run = run_schedule(schedule, operator=request.user, trigger_mode="manual")
    return JsonResponse(
        {
            "success": True,
            "run": {
                "id": run.id,
                "status": run.status,
                "summary": run.summary,
                "total_count": run.total_count,
                "success_count": run.success_count,
                "skipped_count": run.skipped_count,
                "failed_count": run.failed_count,
            },
        }
    )


@login_required
@require_http_methods(["POST"])
def schedule_run_due_api(request):
    if not can_do_action(request.user, "schedule_manage"):
        return JsonResponse({"success": False, "error": "仅管理员可操作"}, status=403)
    now = timezone.localtime()
    schedules = BranchCreateSchedule.objects.filter(enabled=True).order_by("id")
    executed = 0
    for schedule in schedules:
        if not _cron_matches(schedule.cron_expr.strip(), now):
            continue
        last_run_at = schedule.last_run_at
        if last_run_at:
            last_local = timezone.localtime(last_run_at)
            if (
                last_local.year == now.year
                and last_local.month == now.month
                and last_local.day == now.day
                and last_local.hour == now.hour
                and last_local.minute == now.minute
            ):
                continue
        run_schedule(schedule, operator=schedule.created_by, trigger_mode="cron")
        executed += 1
    return JsonResponse({"success": True, "executed": executed})
