"""
投产追版 - 视图
"""
import subprocess
import sys
import time
import uuid
import json
from functools import wraps
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from django.contrib import messages
from django.utils import timezone

from accounts.permissions import can_do_action
from accounts.services.git_settings import get_runtime_git_settings
from branch_create.models import ReleaseBatch, ReleaseItem
from branch_create.services.release_track_service import (
    RELEASE_TRACK_APPROVAL_URL,
    ReleaseTrackOptions,
    ReleaseTrackService,
    build_config_text_from_file,
)
from .models import ReleaseTrackRun, ReleaseTrackRunItem

def admin_required(view_func):
    """统一权限策略：投产追版执行权限。"""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(settings.LOGIN_URL)
        if not can_do_action(request.user, "release_track_use"):
            messages.error(request, "你没有投产追版权限。")
            return redirect("/")
        return view_func(request, *args, **kwargs)

    return _wrapped
from .gitlab_api import GitLabConfig, GitLabAPI
from .config_parser import parse_release_config


def _git(*args, cwd=None):
    result = subprocess.run(
        ["git"] + list(args), cwd=cwd, capture_output=True, text=True, timeout=120
    )
    return result


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


def _ensure_api() -> GitLabAPI:
    runtime = get_runtime_git_settings()
    if not runtime.git_pat and not (runtime.git_username and runtime.git_password):
        raise Exception("请先在管理员配置中设置 GitLab PAT 或用户名/密码")
    cfg = GitLabConfig(
        base_url=runtime.git_base_url,
        group=runtime.git_group,
        token=runtime.git_pat,
        username=runtime.git_username,
        password=runtime.git_password,
    )
    return GitLabAPI(cfg)


# ============================================================================
# 页面
# ============================================================================


@login_required
@admin_required
def release_track_index(request):
    return render(request, "release_track/index.html")


@login_required
@admin_required
def release_track_execute(request):
    if request.method == "POST":
        config_text = request.POST.get("config_text", "").strip()
        if not config_text:
            messages.error(request, "配置内容不能为空")
            return redirect("release_track:index")

        try:
            config = parse_release_config(config_text)
        except Exception as e:
            messages.error(request, f"配置解析失败: {e}")
            return redirect("release_track:index")

        if not config.tag_name:
            messages.error(request, "配置缺少 TAG_NAME")
            return redirect("release_track:index")

        if not config.repos:
            messages.error(request, "配置中没有可执行的仓库")
            return redirect("release_track:index")

        results = _run_premerge_check(config)
        return render(request, "release_track/result.html", {
            "config": config,
            "results": results,
        })

    return redirect("release_track:index")


# ============================================================================
# API
# ============================================================================


@login_required
@admin_required
@require_http_methods(["POST"])
def release_track_api_precheck(request):
    config_text = request.POST.get("config_text", "").strip()
    if not config_text:
        return JsonResponse({"success": False, "error": "配置内容为空"})

    try:
        config = parse_release_config(config_text)
        results = _run_premerge_check(config)
        return JsonResponse({
            "success": True,
            "config": {
                "tag_name": config.tag_name,
                "merge_message": config.merge_message,
                "tag_message": config.tag_message,
            },
            "results": [_result_to_dict(r) for r in results],
        })
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


@login_required
@admin_required
@require_http_methods(["POST"])
def release_track_api_create_mr(request):
    repo = request.POST.get("repo", "").strip()
    source_branch = request.POST.get("source_branch", "").strip()
    target_branch = request.POST.get("target_branch", "").strip()
    config_text = request.POST.get("config_text", "").strip()

    if not all([repo, source_branch, target_branch]):
        return JsonResponse({"success": False, "error": "参数不完整"})

    try:
        cfg = parse_release_config(config_text) if config_text else None
        merge_msg = (cfg.merge_message if cfg else "合并")
        tag_name = (cfg.tag_name if cfg else "merge")

        api = _ensure_api()
        title = f"{tag_name}-{repo}"
        description = f"repo={repo}; source={source_branch}; target={target_branch}"

        mr_resp = api.create_mr(
            repo=repo,
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
        )

        mr_url = mr_resp.get("web_url", "")
        mr_iid = mr_resp.get("iid", 0)

        merge_status = "pending"
        if mr_iid:
            try:
                api.merge_mr(repo=repo, mr_iid=mr_iid, merge_commit_message=merge_msg)
                merge_status = "merged"
            except Exception as e:
                merge_status = f"failed: {e}"

        return JsonResponse({
            "success": True,
            "mr_url": mr_url,
            "mr_iid": mr_iid,
            "merge_status": merge_status,
        })
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


@login_required
@admin_required
@require_http_methods(["POST"])
def release_track_api_create_tag(request):
    repo = request.POST.get("repo", "").strip()
    target_branch = request.POST.get("target_branch", "").strip()
    config_text = request.POST.get("config_text", "").strip()
    force = request.POST.get("force", "false") == "true"

    if not all([repo, target_branch]):
        return JsonResponse({"success": False, "error": "参数不完整"})

    try:
        cfg = parse_release_config(config_text) if config_text else None
        api = _ensure_api()
        tag_name = (cfg.tag_name if cfg else "unknown")
        tag_message = (cfg.tag_message if cfg else "")

        api.force_push_tag(
            repo=repo,
            tag_name=tag_name,
            ref=target_branch,
            message=tag_message,
        )

        return JsonResponse({
            "success": True,
            "tag_name": tag_name,
            "message": tag_message,
        })
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


# ============================================================================
# 内部逻辑
# ============================================================================


class PrecheckResult:
    def __init__(self, repo: str, release_branch: str, target_branch: str):
        self.repo = repo
        self.release_branch = release_branch
        self.target_branch = target_branch
        self.status = "pending"
        self.reason = ""
        self.pending_commits = 0
        self.pending_log = ""
        self.latest_commit = ""


def _run_premerge_check(config) -> list:
    runtime = get_runtime_git_settings()
    work_base_path, _ = runtime.resolve_writable_work_base_path()
    results = []

    for repo_cfg in config.repos:
        result = PrecheckResult(
            repo=repo_cfg.repo,
            release_branch=repo_cfg.release_branch,
            target_branch=repo_cfg.target_branch,
        )

        if repo_cfg.commented:
            result.status = "skipped"
            result.reason = "配置中已注释跳过"
            results.append(result)
            continue

        repo_dir = work_base_path / repo_cfg.repo

        if not repo_dir.exists():
            result.status = "error"
            result.reason = f"仓库目录不存在（需在 {work_base_path} 下存在）"
            results.append(result)
            continue
        if not (repo_dir / ".git").exists():
            result.status = "error"
            result.reason = "目录存在但不是 Git 仓库"
            results.append(result)
            continue

        try:
            set_url = _git(
                "remote",
                "set-url",
                "origin",
                runtime.with_credentials_url(repo_cfg.repo),
                cwd=repo_dir,
            )
            if set_url.returncode != 0:
                result.status = "error"
                result.reason = "设置仓库远端地址失败"
                results.append(result)
                continue

            # 检查工作区
            st = _git("status", "--porcelain", cwd=repo_dir)
            if st.stdout.strip():
                result.status = "error"
                result.reason = "工作区存在未提交改动"
                results.append(result)
                continue

            # fetch
            fr = _git("fetch", "origin", "--tags", "--prune", cwd=repo_dir)
            if fr.returncode != 0:
                result.status = "error"
                result.reason = "git fetch 失败"
                results.append(result)
                continue

            # 检查投产分支存在
            br_check = _git(
                "ls-remote", "--exit-code", "--heads",
                "origin", repo_cfg.release_branch, cwd=repo_dir,
            )
            if br_check.returncode != 0:
                result.status = "error"
                result.reason = f"远端不存在投产分支: {repo_cfg.release_branch}"
                results.append(result)
                continue

            # 切换到 release 分支并更新
            local_br = _git(
                "show-ref", "--verify",
                f"refs/heads/{repo_cfg.release_branch}", cwd=repo_dir,
            )
            if local_br.returncode == 0:
                _git("checkout", repo_cfg.release_branch, cwd=repo_dir)
            else:
                _git("checkout", "-b", repo_cfg.release_branch,
                     f"origin/{repo_cfg.release_branch}", cwd=repo_dir)
            _git("pull", "--ff-only", "origin", repo_cfg.release_branch, cwd=repo_dir)

            # 切换到 target 分支并更新
            _git("checkout", repo_cfg.target_branch, cwd=repo_dir)
            pull = _git(
                "pull", "--ff-only", "origin", repo_cfg.target_branch,
                cwd=repo_dir,
            )
            if pull.returncode != 0:
                result.status = "error"
                result.reason = f"更新目标分支失败: {repo_cfg.target_branch}"
                results.append(result)
                continue

            # 检查待合并提交
            log_result = _git(
                "log", "--oneline",
                f"origin/{repo_cfg.target_branch}..{repo_cfg.release_branch}",
                cwd=repo_dir,
            )
            pending_log = log_result.stdout.strip()
            pending_lines = [l for l in pending_log.splitlines() if l.strip()]
            result.pending_commits = len(pending_lines)
            result.pending_log = pending_log

            if not pending_log:
                result.status = "skipped"
                result.reason = "无待合并提交"
                results.append(result)
                continue

            # 试合并
            merge_result = _git(
                "merge", "--no-commit", "--no-ff", repo_cfg.release_branch,
                cwd=repo_dir,
            )
            if merge_result.returncode != 0:
                _git("merge", "--abort", cwd=repo_dir)
                result.status = "conflict"
                result.reason = "本地 merge 冲突"
                results.append(result)
                continue

            _git("merge", "--abort", cwd=repo_dir)
            head = _git("log", "-1", "--oneline", cwd=repo_dir)
            result.latest_commit = head.stdout.strip()
            result.status = "success"
            result.reason = "本地预合并成功"
            results.append(result)

        except subprocess.TimeoutExpired:
            result.status = "error"
            result.reason = "执行超时"
            results.append(result)
        except Exception as e:
            result.status = "error"
            result.reason = str(e)
            results.append(result)

    return results


def _result_to_dict(r: PrecheckResult) -> dict:
    return {
        "repo": r.repo,
        "release_branch": r.release_branch,
        "target_branch": r.target_branch,
        "status": r.status,
        "reason": r.reason,
        "pending_commits": r.pending_commits,
        "pending_log": r.pending_log,
        "latest_commit": r.latest_commit,
    }


def _default_release_track_config_file() -> str:
    return str(Path(settings.BASE_DIR).parent / "功能" / "投产追版" / "repos.conf")


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _parse_selected_projects(value: str | None) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return []
    # 优先兼容 JSON 数组
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
    # 兼容逗号分隔
    return [part.strip() for part in raw.split(",") if part.strip()]


def _get_completed_repo_codes_for_batch(batch_id: int) -> set[str]:
    completed_repos = (
        ReleaseTrackRunItem.objects.filter(
            run__batch_id=batch_id,
            run__status=ReleaseTrackRun.Status.SUCCESS,
            status="SUCCESS",
        )
        .values_list("repo", flat=True)
        .distinct()
    )
    return {str(repo).strip() for repo in completed_repos if str(repo).strip()}


def _run_to_dict(run: ReleaseTrackRun) -> dict:
    rows = [
        {
            "repo": item.repo,
            "release_branch": item.release_branch,
            "target_branch": item.target_branch,
            "stage": item.stage,
            "status": item.status,
            "reason": item.reason,
            "pending_count": item.pending_count,
            "mr_url": item.mr_url,
            "mr_iid": item.mr_iid,
            "mr_state": item.mr_state,
            "tag_result": item.tag_result,
            "source": item.source,
        }
        for item in run.items.order_by("repo", "id")
    ]
    return {
        "run_id": run.run_id,
        "status": run.status,
        "phase": run.phase,
        "approval_status": run.approval_status,
        "approval_url": run.approval_url,
        "approved_by": run.approved_by.username if run.approved_by_id else "",
        "approved_at": run.approved_at.isoformat() if run.approved_at else "",
        "batch_id": run.batch_id,
        "tag_name": run.tag_name,
        "merge_message": run.merge_message,
        "tag_message": run.tag_message,
        "total": run.total_count,
        "processed": run.processed_count,
        "success": run.success_count,
        "skipped": run.skipped_count,
        "failed": run.failed_count,
        "tip": run.tip,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else "",
        "finished_at": run.finished_at.isoformat() if run.finished_at else "",
        "rows": rows,
    }


def _upsert_run_items_from_summary(run: ReleaseTrackRun, summary: dict) -> None:
    states = (summary or {}).get("states") or {}
    for repo, state in states.items():
        ReleaseTrackRunItem.objects.update_or_create(
            run=run,
            repo=repo,
            defaults={
                "release_branch": state.get("release_branch", ""),
                "target_branch": state.get("target_branch", ""),
                "stage": state.get("stage", ""),
                "status": state.get("status", ""),
                "reason": state.get("reason", "")[:255],
                "pending_count": int(state.get("pending_count") or 0),
                "mr_url": state.get("mr_url", "")[:255],
                "mr_iid": int(state.get("mr_iid") or 0),
                "mr_state": state.get("mr_state", "")[:32],
                "tag_result": state.get("tag_result", "")[:255],
                "source": state.get("source", "")[:32],
            },
        )


def _run_release_track_worker(run_id: str, options: ReleaseTrackOptions) -> None:
    run = ReleaseTrackRun.objects.filter(run_id=run_id).first()
    if not run:
        return

    def wait_for_approval(approval_url: str) -> bool:
        run.approval_url = approval_url[:255]
        run.approval_status = "pending"
        run.phase = "approval"
        run.tip = "等待管理员审批"
        run.save(update_fields=["approval_url", "approval_status", "phase", "tip", "updated_at"])
        while True:
            run.refresh_from_db()
            if run.approval_status == "approved":
                run.tip = "审批通过，继续执行后续阶段"
                run.save(update_fields=["tip", "updated_at"])
                return True
            if run.approval_status == "rejected":
                return False
            if run.status != ReleaseTrackRun.Status.RUNNING:
                return False
            time.sleep(1)

    def on_event(event: dict) -> None:
        event_type = event.get("event")
        if event_type == "phase":
            run.phase = str(event.get("phase") or run.phase)
            run.tip = f"当前阶段: {run.phase}"
            run.save(update_fields=["phase", "tip", "updated_at"])
            return

        if event_type == "approval":
            run.approval_url = str(event.get("url") or "")[:255]
            run.approval_status = "pending"
            run.tip = f"等待审批: {run.approval_url}"
            run.save(update_fields=["approval_url", "approval_status", "tip", "updated_at"])
            return

        if event_type == "summary":
            summary = event.get("summary") or {}
            _upsert_run_items_from_summary(run, summary)
            states = summary.get("states") or {}
            total = len(states)
            success = len(summary.get("success_repos") or [])
            skipped = len(summary.get("skipped_repos") or [])
            failed = len(summary.get("failed_repos") or [])
            processed = success + skipped + failed
            run.phase = str(summary.get("current_phase") or run.phase)
            run.total_count = total
            run.processed_count = processed
            run.success_count = success
            run.skipped_count = skipped
            run.failed_count = failed
            run.tip = f"执行中 {processed}/{total}"
            run.save(
                update_fields=[
                    "phase",
                    "total_count",
                    "processed_count",
                    "success_count",
                    "skipped_count",
                    "failed_count",
                    "tip",
                    "updated_at",
                ]
            )

    try:
        service = ReleaseTrackService(
            options=options,
            output=lambda _: None,
            event_callback=on_event,
            approval_callback=wait_for_approval,
        )
        summary = service.run()
        run.total_count = len(summary.states)
        run.processed_count = len(summary.success_repos) + len(summary.skipped_repos) + len(summary.failed_repos)
        run.success_count = len(summary.success_repos)
        run.skipped_count = len(summary.skipped_repos)
        run.failed_count = len(summary.failed_repos)
        run.phase = "done"
        run.status = (
            ReleaseTrackRun.Status.FAILED
            if summary.has_failures()
            else ReleaseTrackRun.Status.SUCCESS
        )
        run.tip = (
            f"执行完成：成功 {run.success_count}，跳过 {run.skipped_count}，失败 {run.failed_count}"
        )
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "phase",
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
        run.status = ReleaseTrackRun.Status.FAILED
        run.phase = "failed"
        run.error = str(exc)[:255]
        run.tip = f"执行失败：{exc}"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "phase", "error", "tip", "finished_at", "updated_at"])


@login_required
@admin_required
@require_http_methods(["GET"])
def release_track_api_batches(request):
    batches = ReleaseBatch.objects.filter(status=ReleaseBatch.Status.OPEN).order_by("-release_date", "-id")
    data = []
    for batch in batches:
        marked_count = ReleaseItem.objects.filter(batch=batch, rel_deployed=True).count()
        data.append(
            {
                "id": batch.id,
                "release_date": str(batch.release_date),
                "release_branch": batch.release_branch,
                "release_type": batch.release_type,
                "status": batch.status,
                "marked_count": marked_count,
            }
        )
    return JsonResponse(
        {
            "success": True,
            "default_config_file": _default_release_track_config_file(),
            "batches": data,
        }
    )


@login_required
@admin_required
@require_http_methods(["GET"])
def release_track_api_batch_detail(request):
    batch_id_raw = (request.GET.get("batch_id") or "").strip()
    if not batch_id_raw:
        return JsonResponse({"success": False, "error": "batch_id 必填"}, status=400)
    try:
        batch_id = int(batch_id_raw)
    except ValueError:
        return JsonResponse({"success": False, "error": "batch_id 非法"}, status=400)

    batch = ReleaseBatch.objects.filter(pk=batch_id).first()
    if not batch:
        return JsonResponse({"success": False, "error": "批次不存在"}, status=404)

    # 仅展示投产征集里已登记（有 ReleaseItem）的工程，不展示未登记工程。
    items = (
        ReleaseItem.objects.select_related("project")
        .filter(batch=batch)
        .values(
            "project_id",
            "project__project_code",
            "project__project_name",
        )
        .annotate(
            item_count=Count("id"),
            deployed_marked_count=Count("id", filter=Q(rel_deployed=True)),
        )
        .order_by("project__project_name", "project_id")
    )
    completed_repo_codes = _get_completed_repo_codes_for_batch(batch.id)
    rows = [
        {
            "project_id": row["project_id"],
            "project_code": row["project__project_code"],
            "project_name": row["project__project_name"],
            "release_branch": batch.release_branch,
            "item_count": row["item_count"],
            "deployed_marked_count": row["deployed_marked_count"],
            "track_completed": row["project__project_code"] in completed_repo_codes,
        }
        for row in items
    ]

    return JsonResponse(
        {
            "success": True,
            "batch": {
                "id": batch.id,
                "release_date": str(batch.release_date),
                "release_branch": batch.release_branch,
                "release_type": batch.release_type,
                "status": batch.status,
            },
            "projects": rows,
        }
    )


@login_required
@admin_required
@require_http_methods(["POST"])
def release_track_api_run_start(request):
    batch_id_raw = (request.POST.get("batch_id") or "").strip()
    if not batch_id_raw:
        return JsonResponse({"success": False, "error": "batch_id 必填"}, status=400)
    try:
        batch_id = int(batch_id_raw)
    except ValueError:
        return JsonResponse({"success": False, "error": "batch_id 非法"}, status=400)

    config_text = (request.POST.get("config_text") or "").strip()
    config_file = (request.POST.get("config_file") or "").strip() or _default_release_track_config_file()
    if not config_text:
        try:
            config_text = build_config_text_from_file(config_file)
        except Exception as exc:  # noqa: BLE001
            return JsonResponse({"success": False, "error": str(exc)}, status=400)

    selected_projects_raw = request.POST.get("selected_projects")
    selected_projects = _parse_selected_projects(selected_projects_raw)
    if selected_projects_raw is not None and not selected_projects:
        return JsonResponse({"success": False, "error": "请先勾选要追板的项目"}, status=400)
    completed_selected = sorted(
        {
            code
            for code in selected_projects
            if code in _get_completed_repo_codes_for_batch(batch_id)
        }
    )
    if completed_selected:
        return JsonResponse(
            {
                "success": False,
                "error": f"以下项目已追板完成，请勿重复执行：{', '.join(completed_selected)}",
            },
            status=400,
        )
    options = ReleaseTrackOptions(
        batch_id=batch_id,
        config_text=config_text,
        tag_name=(request.POST.get("tag_name") or "").strip(),
        merge_message=(request.POST.get("merge_message") or "").strip(),
        tag_message=(request.POST.get("tag_message") or "").strip(),
        auto_merge_mr=False,
        force_tag=False,
        assume_yes=True,
        approval_url=RELEASE_TRACK_APPROVAL_URL,
        default_target_branch=(request.POST.get("default_target_branch") or "master").strip() or "master",
        work_base_dir=(request.POST.get("work_base_dir") or "").strip(),
        dry_run=_parse_bool(request.POST.get("dry_run"), default=False),
        selected_projects=selected_projects,
    )

    run_id = uuid.uuid4().hex
    run = ReleaseTrackRun.objects.create(
        run_id=run_id,
        status=ReleaseTrackRun.Status.RUNNING,
        phase="init",
        approval_status="pending",
        triggered_by=request.user,
        batch_id=batch_id,
        tag_name=options.tag_name,
        merge_message=options.merge_message,
        tag_message=options.tag_message,
        tip="任务已启动",
    )

    payload_file = _create_job_payload_file(
        "release-track",
        {
            "batch_id": options.batch_id,
            "config_text": options.config_text,
            "tag_name": options.tag_name,
            "merge_message": options.merge_message,
            "tag_message": options.tag_message,
            "auto_merge_mr": options.auto_merge_mr,
            "force_tag": options.force_tag,
            "assume_yes": options.assume_yes,
            "approval_url": options.approval_url,
            "default_target_branch": options.default_target_branch,
            "work_base_dir": options.work_base_dir,
            "dry_run": options.dry_run,
            "selected_projects": options.selected_projects,
        },
    )
    _spawn_detached_command("run_release_track_run", run_id, str(payload_file))
    return JsonResponse({"success": True, "run_id": run.run_id})


@login_required
@admin_required
@require_http_methods(["GET"])
def release_track_api_run_progress(request):
    run_id = (request.GET.get("run_id") or "").strip()
    if not run_id:
        return JsonResponse({"success": False, "error": "run_id 必填"}, status=400)
    run = (
        ReleaseTrackRun.objects.select_related("triggered_by")
        .prefetch_related("items")
        .filter(run_id=run_id)
        .first()
    )
    if not run:
        return JsonResponse({"success": False, "error": "执行任务不存在"}, status=404)
    if run.triggered_by_id and run.triggered_by_id != request.user.id and not request.user.is_superuser:
        return JsonResponse({"success": False, "error": "无权限查看该执行记录"}, status=403)
    return JsonResponse({"success": True, "run": _run_to_dict(run)})


@login_required
@admin_required
@require_http_methods(["POST"])
def release_track_api_run_approve(request):
    run_id = (request.POST.get("run_id") or "").strip()
    action = (request.POST.get("action") or "").strip().lower()
    if not run_id:
        return JsonResponse({"success": False, "error": "run_id 必填"}, status=400)
    if action not in {"approve", "reject"}:
        return JsonResponse({"success": False, "error": "action 非法"}, status=400)
    run = ReleaseTrackRun.objects.filter(run_id=run_id).first()
    if not run:
        return JsonResponse({"success": False, "error": "执行任务不存在"}, status=404)
    if run.status != ReleaseTrackRun.Status.RUNNING:
        return JsonResponse({"success": False, "error": "当前任务已结束，无法审批"}, status=400)
    if run.phase != "approval":
        return JsonResponse({"success": False, "error": "当前任务不在审批阶段"}, status=400)
    run.approval_status = "approved" if action == "approve" else "rejected"
    run.approved_by = request.user
    run.approved_at = timezone.now()
    run.tip = "审批通过，等待继续执行" if action == "approve" else "审批驳回，任务将终止"
    run.save(update_fields=["approval_status", "approved_by", "approved_at", "tip", "updated_at"])
    return JsonResponse({"success": True, "run": _run_to_dict(run)})
