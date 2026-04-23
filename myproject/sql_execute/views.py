from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import close_old_connections, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from accounts.models import GitPlatformConfig, RolePermissionPolicy
from accounts.permissions import (
    apply_data_scope,
    can_access_menu,
    can_do_action,
    get_data_scope,
)
from branch_create.models import ReleaseBatch

from .models import SqlExecutionRequest
from .services import ProgressEvent, execute_sql_request

_MAX_EXECUTION_LOG_CHARS = 100_000
_MAX_SQL_PREVIEW_CHARS = 200_000
_SQL_REPO_BRANCH = "rel执行且投产SQL"


def _spawn_detached_command(*args: str) -> None:
    subprocess.Popen(  # noqa: S603
        [sys.executable, "manage.py", *args],
        cwd=str(settings.BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _truncate_execution_log(text: str) -> str:
    if len(text) <= _MAX_EXECUTION_LOG_CHARS:
        return text
    marker = "...[日志已截断]\n"
    keep = _MAX_EXECUTION_LOG_CHARS - len(marker)
    return marker + text[-keep:]


def _can_access(user) -> bool:
    return can_access_menu(user, "sql_execute")


def _can_apply(user) -> bool:
    return can_do_action(user, "sql_request_apply")


def _can_approve(user) -> bool:
    return can_do_action(user, "sql_request_approve")


def _can_view_request_progress(user, row: SqlExecutionRequest) -> bool:
    if not _can_access(user):
        return False
    if user.is_superuser:
        return True
    if can_do_action(user, "sql_request_edit_others"):
        return True
    if get_data_scope(user, "sql_requests") == RolePermissionPolicy.DataScope.ALL:
        return True
    return row.requested_by_id == user.id


def _get_repo_path() -> Path | None:
    config = GitPlatformConfig.get_solo_safe()
    raw = (config.sql_repo_path or "").strip()
    if not raw:
        return None
    return Path(raw).resolve()


def _git_run(repo_path: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ensure_sql_repo_branch(repo_path: Path, *, pull_remote: bool = False) -> tuple[bool, str]:
    if not (repo_path / ".git").exists():
        return False, "仓库路径不是 Git 仓库"

    # 尽量先拿到远端分支信息，避免首次切分支失败。
    fetch_res = _git_run(repo_path, "fetch", "origin", _SQL_REPO_BRANCH, timeout=180)
    if fetch_res.returncode != 0:
        return False, fetch_res.stderr.strip() or f"拉取分支 {_SQL_REPO_BRANCH} 失败"

    checkout_res = _git_run(repo_path, "checkout", _SQL_REPO_BRANCH)
    if checkout_res.returncode != 0:
        create_res = _git_run(
            repo_path,
            "checkout",
            "-b",
            _SQL_REPO_BRANCH,
            f"origin/{_SQL_REPO_BRANCH}",
        )
        if create_res.returncode != 0:
            return False, create_res.stderr.strip() or f"切换分支 {_SQL_REPO_BRANCH} 失败"

    if not pull_remote:
        return True, ""
    pull_res = _git_run(repo_path, "pull", "--ff-only", "origin", _SQL_REPO_BRANCH)
    if pull_res.returncode != 0:
        return False, pull_res.stderr.strip() or "同步失败"
    return True, pull_res.stdout.strip() or "同步完成"


def _release_date_to_repo_dir_name(release_date_raw: str) -> str | None:
    value = (release_date_raw or "").strip()
    parsed = parse_date(value)
    if not parsed:
        return None
    return parsed.strftime("%Y%m%d")


def _has_sql_files_in_directory(folder: Path) -> bool:
    return any(path.is_file() for path in folder.glob("*.sql"))


def _list_sql_directories_by_release_date(repo_path: Path, release_dir: str) -> list[str]:
    date_root = (repo_path / release_dir).resolve()
    try:
        date_root.relative_to(repo_path)
    except ValueError:
        return []
    if not date_root.exists() or not date_root.is_dir():
        return []

    directories: list[str] = []
    for child in sorted(date_root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        if not _has_sql_files_in_directory(child):
            continue
        try:
            relative = child.relative_to(repo_path)
        except ValueError:
            continue
        directories.append(str(relative))
    return directories


def _list_sql_files(repo_path: Path, folder: str) -> list[str]:
    folder_abs = (repo_path / folder).resolve()
    try:
        folder_abs.relative_to(repo_path)
    except ValueError:
        return []
    if not folder_abs.exists() or not folder_abs.is_dir():
        return []
    files = []
    for file_path in sorted(folder_abs.glob("*.sql")):
        files.append(file_path.name)
    return files


def _nearest_future_release_date_str(release_dates: list, today) -> str:
    future_dates = [value for value in release_dates if value and value >= today]
    if not future_dates:
        return ""
    return str(min(future_dates))


def _serialize_request(row: SqlExecutionRequest) -> dict:
    selected_files = _request_selected_files(row)
    return {
        "id": row.id,
        "release_date": str(row.release_date),
        "folder_path": row.folder_path,
        "selected_files": selected_files,
        "status": row.status,
        "status_label": row.get_status_display(),
        "execution_result": row.execution_result,
        "execution_tip": row.execution_tip,
        "execution_log": row.execution_log,
        "requested_by": row.requested_by.username,
        "approved_by": row.approved_by.username if row.approved_by_id else "",
        "created_at": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M:%S"),
        "executed_at": timezone.localtime(row.executed_at).strftime("%Y-%m-%d %H:%M:%S")
        if row.executed_at
        else "",
    }


def _request_selected_files(row: SqlExecutionRequest) -> list[str]:
    try:
        parsed = json.loads(row.selected_files_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    files: list[str] = []
    for item in parsed:
        raw = str(item).strip()
        if not raw:
            continue
        path_obj = Path(raw)
        folder = Path((row.folder_path or "").strip()).resolve()
        try:
            resolved = path_obj.resolve() if path_obj.is_absolute() else (folder / path_obj).resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(folder)
        except ValueError:
            continue
        files.append(str(resolved))
    return files


def _safe_read_sql_file_for_request(row: SqlExecutionRequest, file_path: str) -> tuple[bool, str]:
    normalized_path = (file_path or "").strip()
    if not normalized_path:
        return False, "file_path 不能为空"
    if not normalized_path.lower().endswith(".sql"):
        return False, "仅支持查看 .sql 文件"

    folder = Path((row.folder_path or "").strip()).resolve()
    if not folder.exists() or not folder.is_dir():
        return False, "SQL 目录不存在"

    selected_candidates: set[Path] = set()
    for selected in _request_selected_files(row):
        selected_obj = Path(selected)
        candidate = (
            selected_obj.resolve()
            if selected_obj.is_absolute()
            else (folder / selected_obj).resolve()
        )
        try:
            candidate.relative_to(folder)
        except ValueError:
            continue
        selected_candidates.add(candidate)

    target_obj = Path(normalized_path)
    target = target_obj.resolve() if target_obj.is_absolute() else (folder / target_obj).resolve()
    try:
        target.relative_to(folder)
    except ValueError:
        return False, "文件路径非法"
    if target not in selected_candidates:
        return False, "该文件不在本次申请勾选范围内"
    if not target.exists() or not target.is_file():
        return False, "SQL 文件不存在"

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"读取 SQL 文件失败：{exc}"
    if len(content) > _MAX_SQL_PREVIEW_CHARS:
        content = content[:_MAX_SQL_PREVIEW_CHARS] + "\n\n-- [预览已截断]"
    return True, content


def _sql_execute_worker(request_id: int) -> None:
    close_old_connections()
    try:
        row = SqlExecutionRequest.objects.filter(pk=request_id).first()
        if not row or row.status != SqlExecutionRequest.Status.RUNNING:
            return

        def on_progress(evt: ProgressEvent) -> None:
            close_old_connections()
            current = SqlExecutionRequest.objects.filter(pk=request_id).first()
            if not current:
                return
            new_log = current.execution_log or ""
            if evt.get("log"):
                new_log = _truncate_execution_log(new_log + str(evt["log"]))
            new_tip = current.execution_tip
            if evt.get("tip") is not None:
                new_tip = str(evt["tip"])[:255]
            SqlExecutionRequest.objects.filter(pk=request_id).update(
                execution_log=new_log,
                execution_tip=new_tip,
                updated_at=timezone.now(),
            )

        success, result_message, log_text = execute_sql_request(
            row.folder_path,
            row.selected_files_json,
            progress_callback=on_progress,
        )
        final_tip = "执行完成" if success else "执行失败"
        SqlExecutionRequest.objects.filter(pk=request_id).update(
            status=SqlExecutionRequest.Status.SUCCESS
            if success
            else SqlExecutionRequest.Status.FAILED,
            execution_result=(result_message or "")[:255],
            execution_log=_truncate_execution_log(log_text),
            execution_tip=final_tip,
            executed_at=timezone.now(),
            updated_at=timezone.now(),
        )
    except Exception as exc:  # noqa: BLE001
        current = SqlExecutionRequest.objects.filter(pk=request_id).first()
        err_tail = f"\n[WORKER_ERROR] {exc}\n"
        merged_log = _truncate_execution_log((current.execution_log if current else "") + err_tail)
        SqlExecutionRequest.objects.filter(pk=request_id).update(
            status=SqlExecutionRequest.Status.FAILED,
            execution_result=str(exc)[:255],
            execution_log=merged_log,
            execution_tip="执行失败",
            executed_at=timezone.now(),
            updated_at=timezone.now(),
        )
    finally:
        close_old_connections()


@login_required
def sql_execute_page(request):
    if not _can_access(request.user):
        messages.error(request, "无 SQL 执行功能访问权限。")
        return redirect("/")

    today = timezone.localdate()
    default_start = today - timedelta(days=30)
    default_end = today + timedelta(days=30)

    release_dates = list(
        ReleaseBatch.objects.order_by("-release_date")
        .values_list("release_date", flat=True)
        .distinct()
    )
    release_date_options = [str(value) for value in release_dates]
    apply_default_release_date = _nearest_future_release_date_str(release_dates, today)

    start_date_raw = (request.GET.get("start_date") or str(default_start)).strip()
    end_date_raw = (request.GET.get("end_date") or str(default_end)).strip()
    applicant_raw = (request.GET.get("applicant") or "").strip()
    folder_raw = (request.GET.get("folder") or "").strip()
    release_date_raw = (request.GET.get("release_date") or "").strip()
    status_raw = (request.GET.get("status") or "").strip().lower()
    allowed_status_filters = {
        SqlExecutionRequest.Status.PENDING,
        SqlExecutionRequest.Status.SUCCESS,
        SqlExecutionRequest.Status.FAILED,
    }
    status_filter = status_raw if status_raw in allowed_status_filters else ""
    start_date = parse_date(start_date_raw) or default_start
    end_date = parse_date(end_date_raw) or default_end

    requests_qs = SqlExecutionRequest.objects.select_related("requested_by", "approved_by")
    if not can_do_action(request.user, "sql_request_edit_others"):
        requests_qs = apply_data_scope(
            requests_qs,
            request.user,
            scope_key="sql_requests",
            owner_field="requested_by",
        )

    requests_qs = requests_qs.filter(release_date__gte=start_date, release_date__lte=end_date)
    if release_date_raw:
        requests_qs = requests_qs.filter(release_date=release_date_raw)
    if status_filter:
        requests_qs = requests_qs.filter(status=status_filter)
    if applicant_raw:
        requests_qs = requests_qs.filter(requested_by__username__icontains=applicant_raw)
    if folder_raw:
        requests_qs = requests_qs.filter(folder_path__icontains=folder_raw)

    rows = [_serialize_request(row) for row in requests_qs[:300]]
    return render(
        request,
        "sql_execute/index.html",
        {
            "can_apply": _can_apply(request.user),
            "can_approve": _can_approve(request.user),
            "release_date_options": release_date_options,
            "apply_default_release_date": apply_default_release_date,
            "rows": rows,
            "filters": {
                "start_date": str(start_date),
                "end_date": str(end_date),
                "release_date": release_date_raw,
                "status": status_filter,
                "applicant": applicant_raw,
                "folder": folder_raw,
            },
        },
    )


@login_required
@require_http_methods(["POST"])
def sql_repo_sync_api(request):
    if not _can_approve(request.user):
        return JsonResponse({"success": False, "error": "无仓库同步权限"}, status=403)
    repo_path = _get_repo_path()
    if not repo_path:
        return JsonResponse({"success": False, "error": "请先在管理员配置 SQL 仓库路径"}, status=400)
    config = GitPlatformConfig.get_solo_safe()
    if not repo_path.exists():
        clone_url = (config.sql_repo_clone_url or "").strip()
        if not clone_url:
            return JsonResponse({"success": False, "error": "仓库路径不存在，且未配置 Clone URL"}, status=400)
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        clone_result = subprocess.run(
            [
                "git",
                "clone",
                "--branch",
                _SQL_REPO_BRANCH,
                "--single-branch",
                clone_url,
                str(repo_path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if clone_result.returncode != 0:
            return JsonResponse(
                {"success": False, "error": clone_result.stderr.strip() or "仓库克隆失败"},
                status=400,
            )
        return JsonResponse({"success": True, "message": f"仓库克隆完成（分支：{_SQL_REPO_BRANCH}）"})

    ok, msg = _ensure_sql_repo_branch(repo_path, pull_remote=True)
    if not ok:
        return JsonResponse({"success": False, "error": msg}, status=400)
    return JsonResponse({"success": True, "message": msg or "同步完成"})


@login_required
@require_http_methods(["GET"])
def sql_repo_folders_api(request):
    if not _can_apply(request.user):
        return JsonResponse({"success": False, "error": "无申请执行权限"}, status=403)
    repo_path = _get_repo_path()
    if not repo_path:
        return JsonResponse({"success": False, "error": "请先在管理员配置 SQL 仓库路径"}, status=400)
    if not repo_path.exists():
        return JsonResponse({"success": False, "error": "SQL 仓库路径不存在"}, status=400)
    ok, msg = _ensure_sql_repo_branch(repo_path, pull_remote=False)
    if not ok:
        return JsonResponse({"success": False, "error": msg}, status=400)
    release_dir = _release_date_to_repo_dir_name(request.GET.get("release_date") or "")
    if not release_dir:
        return JsonResponse({"success": False, "error": "release_date 非法或为空"}, status=400)
    folders = _list_sql_directories_by_release_date(repo_path, release_dir)
    return JsonResponse({"success": True, "repo_path": str(repo_path), "folders": folders})


@login_required
@require_http_methods(["GET"])
def sql_repo_files_api(request):
    if not _can_apply(request.user):
        return JsonResponse({"success": False, "error": "无申请执行权限"}, status=403)
    folder = (request.GET.get("folder") or "").strip()
    if not folder:
        return JsonResponse({"success": False, "error": "folder 必填"}, status=400)
    repo_path = _get_repo_path()
    if not repo_path:
        return JsonResponse({"success": False, "error": "请先在管理员配置 SQL 仓库路径"}, status=400)
    ok, msg = _ensure_sql_repo_branch(repo_path, pull_remote=False)
    if not ok:
        return JsonResponse({"success": False, "error": msg}, status=400)
    files = _list_sql_files(repo_path, folder)
    return JsonResponse({"success": True, "files": files})


@login_required
@require_http_methods(["POST"])
def sql_request_create_api(request):
    if not _can_apply(request.user):
        return JsonResponse({"success": False, "error": "无申请执行权限"}, status=403)
    release_date_raw = (request.POST.get("release_date") or "").strip()
    folder = (request.POST.get("folder") or "").strip()
    selected_files_raw = request.POST.get("selected_files")

    release_date = parse_date(release_date_raw)
    if not release_date:
        return JsonResponse({"success": False, "error": "申请日期无效"}, status=400)
    valid_release_date = ReleaseBatch.objects.filter(release_date=release_date).exists()
    if not valid_release_date:
        return JsonResponse({"success": False, "error": "申请日期必须来自投产征集日期"}, status=400)
    repo_path = _get_repo_path()
    if not repo_path:
        return JsonResponse({"success": False, "error": "请先在管理员配置 SQL 仓库路径"}, status=400)
    folder_abs = (repo_path / folder).resolve()
    try:
        folder_abs.relative_to(repo_path)
    except ValueError:
        return JsonResponse({"success": False, "error": "目录非法"}, status=400)
    if not folder_abs.exists() or not folder_abs.is_dir():
        return JsonResponse({"success": False, "error": "目录不存在"}, status=400)

    valid_files = _list_sql_files(repo_path, folder)
    valid_file_set = set(valid_files)
    if not valid_file_set:
        return JsonResponse({"success": False, "error": "目录下暂无可执行 SQL 文件"}, status=400)

    selected_files = []
    if selected_files_raw not in {None, ""}:
        try:
            selected_files = json.loads(selected_files_raw)
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "error": "selected_files 格式错误"}, status=400)
        if not isinstance(selected_files, list):
            return JsonResponse({"success": False, "error": "selected_files 格式错误"}, status=400)

    normalized_selected: list[str] = []
    if selected_files:
        for file_name in selected_files:
            cleaned = str(file_name).strip()
            if cleaned and cleaned in valid_file_set:
                normalized_selected.append(str((folder_abs / cleaned).resolve()))
    else:
        normalized_selected = [str((folder_abs / file_name).resolve()) for file_name in valid_files]
    if not normalized_selected:
        return JsonResponse({"success": False, "error": "未找到有效 SQL 文件"}, status=400)

    row = SqlExecutionRequest.objects.create(
        release_date=release_date,
        folder_path=str(folder_abs),
        selected_files_json=json.dumps(normalized_selected, ensure_ascii=False),
        status=SqlExecutionRequest.Status.PENDING,
        requested_by=request.user,
    )
    return JsonResponse({"success": True, "id": row.id})


@login_required
@require_http_methods(["GET"])
def sql_request_progress_api(request):
    request_id_raw = (request.GET.get("request_id") or "").strip()
    if not request_id_raw.isdigit():
        return JsonResponse({"success": False, "error": "request_id 非法"}, status=400)
    row = SqlExecutionRequest.objects.select_related("requested_by", "approved_by").filter(
        pk=int(request_id_raw)
    ).first()
    if not row:
        return JsonResponse({"success": False, "error": "申请不存在"}, status=404)
    if not _can_view_request_progress(request.user, row):
        return JsonResponse({"success": False, "error": "无权限查看"}, status=403)
    return JsonResponse({"success": True, "request": _serialize_request(row)})


@login_required
@require_http_methods(["GET"])
def sql_request_file_preview_api(request):
    request_id_raw = (request.GET.get("request_id") or "").strip()
    file_path = (request.GET.get("file_path") or "").strip()
    if not request_id_raw.isdigit():
        return JsonResponse({"success": False, "error": "request_id 非法"}, status=400)
    row = SqlExecutionRequest.objects.select_related("requested_by", "approved_by").filter(
        pk=int(request_id_raw)
    ).first()
    if not row:
        return JsonResponse({"success": False, "error": "申请不存在"}, status=404)
    if not _can_view_request_progress(request.user, row):
        return JsonResponse({"success": False, "error": "无权限查看"}, status=403)
    ok, result = _safe_read_sql_file_for_request(row, file_path)
    if not ok:
        return JsonResponse({"success": False, "error": result}, status=400)
    return JsonResponse(
        {
            "success": True,
            "request_id": row.id,
            "file_path": file_path,
            "content": result,
        }
    )


@login_required
@require_http_methods(["POST"])
def sql_request_action_api(request):
    if not _can_approve(request.user):
        return JsonResponse({"success": False, "error": "无审批权限"}, status=403)
    request_id_raw = (request.POST.get("request_id") or "").strip()
    action = (request.POST.get("action") or "").strip().lower()
    if not request_id_raw.isdigit():
        return JsonResponse({"success": False, "error": "request_id 非法"}, status=400)
    if action not in {"approve", "reject"}:
        return JsonResponse({"success": False, "error": "action 非法"}, status=400)

    with transaction.atomic():
        row = (
            SqlExecutionRequest.objects.select_for_update()
            .filter(pk=int(request_id_raw))
            .first()
        )
        if not row:
            return JsonResponse({"success": False, "error": "申请不存在"}, status=404)
        if row.status != SqlExecutionRequest.Status.PENDING:
            return JsonResponse({"success": False, "error": "当前申请不是待审批状态"}, status=400)

        row.approved_by = request.user
        row.approved_at = timezone.now()
        if action == "reject":
            row.status = SqlExecutionRequest.Status.REJECTED
            row.execution_result = "审批拒绝"
            row.execution_tip = ""
            row.save(
                update_fields=[
                    "approved_by",
                    "approved_at",
                    "status",
                    "execution_result",
                    "execution_tip",
                    "updated_at",
                ]
            )
            return JsonResponse({"success": True, "status": row.status})

        row.status = SqlExecutionRequest.Status.RUNNING
        row.execution_result = "执行中"
        row.execution_tip = "任务已提交后台执行"
        row.execution_log = "任务已提交后台执行…\n"
        row.save(
            update_fields=[
                "approved_by",
                "approved_at",
                "status",
                "execution_result",
                "execution_tip",
                "execution_log",
                "updated_at",
            ]
        )
        request_pk = row.pk

    transaction.on_commit(
        lambda: _spawn_detached_command("run_sql_execute_request", str(request_pk))
    )
    return JsonResponse({"success": True, "status": "running", "request_id": request_pk})
