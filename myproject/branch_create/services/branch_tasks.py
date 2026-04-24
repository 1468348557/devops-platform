from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from branch_create.models import (
    BranchCreateSchedule,
    BranchCreateScheduleRun,
    HoboRequirementLedger,
    ReleaseBatch,
    ReleaseItem,
)
from branch_create.services.branch_executor import (
    BranchExecutor,
    BranchTaskInput,
    normalize_project_code,
)
from accounts.services.git_settings import get_runtime_git_settings


@dataclass
class TaskQueryFilters:
    start_date: str = ""
    end_date: str = ""
    days_back: int = 30
    hobo_description: str = ""
    hobo_requirement_type: str = ""
    hobo_project_id: str = ""
    release_flow_name: str = ""
    release_project_id: str = ""


def _resolve_date_range(start_date_raw: str, end_date_raw: str, days_back: int = 30):
    today = timezone.localdate()
    default_start = today - timedelta(days=days_back)
    start_date = parse_date((start_date_raw or "").strip()) or default_start
    end_date = parse_date((end_date_raw or "").strip()) or today
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def _hobo_tasks(filters: TaskQueryFilters):
    start_date, end_date = _resolve_date_range(filters.start_date, filters.end_date, filters.days_back)
    qs = HoboRequirementLedger.objects.select_related("project").filter(
        branch_created=False,
        applied_date__gte=start_date,
        applied_date__lte=end_date,
    )
    if filters.hobo_description:
        qs = qs.filter(description__icontains=filters.hobo_description.strip())
    if filters.hobo_requirement_type in {c.value for c in HoboRequirementLedger.BranchPrefix}:
        qs = qs.filter(requirement_type=filters.hobo_requirement_type)
    if str(filters.hobo_project_id).strip():
        qs = qs.filter(project_id=filters.hobo_project_id)

    items = []
    for row in qs.order_by("-applied_date", "-id"):
        items.append({
            "key": f"hobo:{row.id}",
            "source_type": "hobo",
            "source_id": row.id,
            "project_code": row.project.project_code,
            "project_name": row.project.project_name or row.project.project_code,
            "new_branch": row.requirement_branch,
            "base_branch": row.base_branch or "master",
            "title": row.description,
            "date": str(row.applied_date),
            "status": "created" if row.branch_created else "pending",
            "error": row.branch_create_error,
            "log": row.branch_create_log,
        })
    return items


def _release_tasks(filters: TaskQueryFilters):
    start_date, end_date = _resolve_date_range(filters.start_date, filters.end_date, filters.days_back)
    qs = ReleaseItem.objects.select_related("project", "batch").filter(
        branch_created=False,
        batch__status=ReleaseBatch.Status.OPEN,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )
    if filters.release_flow_name:
        qs = qs.filter(flow_name__icontains=filters.release_flow_name.strip())
    if str(filters.release_project_id).strip():
        qs = qs.filter(project_id=filters.release_project_id)

    items = []
    for row in qs.order_by("-created_at", "-id"):
        items.append({
            "key": f"release:{row.id}",
            "source_type": "release",
            "source_id": row.id,
            "project_code": row.project.project_code,
            "project_name": row.project.project_name or row.project.project_code,
            "new_branch": row.release_branch,
            "base_branch": "master",
            "title": row.flow_name,
            "date": str(row.created_at.date()),
            "status": "created" if row.branch_created else "pending",
            "error": row.branch_create_error,
            "log": row.branch_create_log,
        })
    return items


def collect_pending_tasks(source_type: str, filters: TaskQueryFilters) -> list[dict]:
    source_type = (source_type or "both").strip().lower()
    if source_type == "hobo":
        return _hobo_tasks(filters)
    if source_type == "release":
        return _release_tasks(filters)
    return _hobo_tasks(filters) + _release_tasks(filters)


def _mark_remote_exists(ref: dict, operator) -> None:
    now = timezone.now()
    reason = f"预览阶段检测到远端分支已存在，自动标记已创建: {ref['new_branch']}"
    if ref["source_type"] == "hobo":
        row = HoboRequirementLedger.objects.filter(pk=ref["source_id"]).first()
    else:
        row = ReleaseItem.objects.filter(pk=ref["source_id"]).first()
    if not row:
        return
    old_log = (row.branch_create_log or "").strip()
    row.branch_created = True
    row.branch_created_at = now
    row.branch_created_by = operator
    row.branch_create_error = ""
    row.branch_create_log = "\n".join([reason, old_log]).strip()[:20000]
    row.save(
        update_fields=[
            "branch_created",
            "branch_created_at",
            "branch_created_by",
            "branch_create_error",
            "branch_create_log",
            "updated_at",
        ]
    )


def _prepare_repo_for_remote_check(
    executor: BranchExecutor,
    runtime,
    work_base_dir,
    project_code: str,
    repo_ready_cache: dict[str, tuple[bool, str, object | None]],
) -> tuple[bool, str, object | None]:
    project = normalize_project_code(project_code)
    cached = repo_ready_cache.get(project)
    if cached:
        return cached

    project_dir = work_base_dir / project
    git_url = runtime.repo_url(project)
    auth_args = tuple(runtime.git_auth_config_args())

    if not project_dir.exists():
        clone = executor._git("clone", git_url, str(project_dir), auth_args=auth_args)
        if clone.returncode != 0:
            repo_ready_cache[project] = (
                False,
                (clone.stderr or clone.stdout or "clone 失败").strip(),
                None,
            )
            return repo_ready_cache[project]
    elif not (project_dir / ".git").exists():
        repo_ready_cache[project] = (False, "目录存在但不是 Git 仓库", None)
        return repo_ready_cache[project]

    origin_set = executor._git(
        "remote",
        "set-url",
        "origin",
        git_url,
        cwd=project_dir,
        auth_args=auth_args,
    )
    if origin_set.returncode != 0:
        repo_ready_cache[project] = (
            False,
            (origin_set.stderr or origin_set.stdout or "设置 origin 失败").strip(),
            None,
        )
        return repo_ready_cache[project]

    repo_ready_cache[project] = (True, "", project_dir)
    return repo_ready_cache[project]


def filter_preview_tasks_with_remote_check(tasks: list[dict], operator) -> tuple[list[dict], int]:
    if not tasks:
        return [], 0

    runtime = get_runtime_git_settings()
    work_base_dir, _ = runtime.resolve_writable_work_base_path()
    auth_args = tuple(runtime.git_auth_config_args())
    executor = BranchExecutor(work_base_dir=str(work_base_dir))
    repo_ready_cache: dict[str, tuple[bool, str, object | None]] = {}
    remote_exists_cache: dict[tuple[str, str], bool] = {}
    kept: list[dict] = []
    auto_marked_count = 0

    for ref in tasks:
        project = normalize_project_code(ref["project_code"])
        branch = ref["new_branch"]
        cache_key = (project, branch)
        exists = remote_exists_cache.get(cache_key)
        if exists is None:
            ok, _, project_dir = _prepare_repo_for_remote_check(
                executor=executor,
                runtime=runtime,
                work_base_dir=work_base_dir,
                project_code=project,
                repo_ready_cache=repo_ready_cache,
            )
            if not ok or project_dir is None:
                kept.append(ref)
                continue
            ls = executor._git(
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                branch,
                cwd=project_dir,
                auth_args=auth_args,
            )
            exists = ls.returncode == 0
            remote_exists_cache[cache_key] = exists

        if exists:
            _mark_remote_exists(ref, operator)
            auto_marked_count += 1
            continue
        kept.append(ref)

    return kept, auto_marked_count


def _mark_task_result(result, operator):
    now = timezone.now()
    is_created = result.status in {"success", "skipped"}
    if result.source_type == "hobo":
        row = HoboRequirementLedger.objects.filter(pk=result.source_id).first()
    else:
        row = ReleaseItem.objects.filter(pk=result.source_id).first()
    if not row:
        return

    row.branch_created = is_created
    row.branch_created_at = now if is_created else None
    row.branch_created_by = operator if is_created else None
    row.branch_create_error = "" if is_created else (result.message or "")[:255]
    row.branch_create_log = (result.log or "")[:20000]
    row.save(
        update_fields=[
            "branch_created",
            "branch_created_at",
            "branch_created_by",
            "branch_create_error",
            "branch_create_log",
            "updated_at",
        ]
    )


def execute_tasks(task_refs: Iterable[dict], operator, progress_callback=None) -> dict:
    executor = BranchExecutor()
    rows = []
    refs = list(task_refs)
    total = len(refs)
    for idx, ref in enumerate(refs, 1):
        task_input = BranchTaskInput(
            source_type=ref["source_type"],
            source_id=int(ref["source_id"]),
            project_code=ref["project_code"],
            new_branch=ref["new_branch"],
            base_branch=ref["base_branch"],
        )
        result = executor.execute(task_input)
        _mark_task_result(result, operator)
        rows.append({
            "source_type": result.source_type,
            "source_id": result.source_id,
            "project_code": result.project_code,
            "new_branch": result.new_branch,
            "status": result.status,
            "message": result.message,
            "log": result.log,
        })
        if progress_callback:
            progress_callback(idx, total, rows[-1])

    success_count = sum(1 for r in rows if r["status"] == "success")
    skipped_count = sum(1 for r in rows if r["status"] == "skipped")
    failed_count = sum(1 for r in rows if r["status"] == "failed")
    return {
        "total": len(rows),
        "success": success_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "rows": rows,
    }


def run_schedule(schedule: BranchCreateSchedule, operator=None, trigger_mode: str = "cron") -> BranchCreateScheduleRun:
    filters = TaskQueryFilters(days_back=schedule.days_back)
    source_type = schedule.source_type
    run = BranchCreateScheduleRun.objects.create(
        schedule=schedule,
        status=BranchCreateScheduleRun.Status.RUNNING,
        trigger_mode=trigger_mode,
        triggered_by=operator,
    )
    try:
        refs = collect_pending_tasks(source_type, filters)
        summary = execute_tasks(refs, operator or schedule.created_by)
        run.total_count = summary["total"]
        run.success_count = summary["success"]
        run.skipped_count = summary["skipped"]
        run.failed_count = summary["failed"]
        run.status = (
            BranchCreateScheduleRun.Status.FAILED
            if summary["failed"] > 0
            else BranchCreateScheduleRun.Status.SUCCESS
        )
        run.summary = (
            f"total={summary['total']} success={summary['success']} "
            f"skipped={summary['skipped']} failed={summary['failed']}"
        )
        run.log = "\n".join(
            f"[{r['status']}] {r['source_type']}#{r['source_id']} {r['project_code']} {r['new_branch']} {r['message']}"
            for r in summary["rows"]
        )
    except Exception as exc:  # noqa: BLE001
        run.status = BranchCreateScheduleRun.Status.FAILED
        run.summary = "调度执行异常"
        run.log = str(exc)
    finally:
        run.finished_at = timezone.now()
        run.save()
        schedule.last_run_at = run.finished_at
        schedule.save(update_fields=["last_run_at", "updated_at"])
    return run
