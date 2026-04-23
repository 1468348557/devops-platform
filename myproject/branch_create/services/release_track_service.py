from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from accounts.services.git_settings import (
    RuntimeGitSettings,
    get_runtime_git_settings,
    scrub_sensitive_text,
)
from branch_create.models import ReleaseBatch, ReleaseItem
from release_track.config_parser import ReleaseConfig, parse_release_config
from release_track.gitlab_api import GitLabAPI, GitLabConfig

RELEASE_TRACK_APPROVAL_URL = "http://gitlab.spdb.com/zh-1087"


class ReleaseTrackError(Exception):
    pass


@dataclass
class ReleaseTrackOptions:
    batch_id: int
    config_text: str = ""
    tag_name: str = ""
    merge_message: str = ""
    tag_message: str = ""
    auto_merge_mr: bool = False
    force_tag: bool = False
    assume_yes: bool = False
    approval_url: str = ""
    default_target_branch: str = "master"
    work_base_dir: str = ""
    dry_run: bool = False
    selected_projects: list[str] = field(default_factory=list)


@dataclass
class RepoPlan:
    repo: str
    release_branch: str
    target_branch: str
    source: str = "batch"


@dataclass
class RepoState:
    repo: str
    release_branch: str
    target_branch: str
    source: str
    status: str = "pending"
    reason: str = ""
    merge_head: str = ""
    pending_count: int = 0
    pending_log: str = ""
    mr_url: str = ""
    mr_iid: int = 0
    mr_state: str = ""
    tag_result: str = ""
    stage: str = "init"


@dataclass
class ReleaseTrackSummary:
    tag_name: str
    merge_message: str
    tag_message: str
    ready_repos: list[str] = field(default_factory=list)
    merged_repos: list[str] = field(default_factory=list)
    success_repos: list[str] = field(default_factory=list)
    skipped_repos: list[str] = field(default_factory=list)
    failed_repos: list[str] = field(default_factory=list)
    mr_created_repos: list[str] = field(default_factory=list)
    tag_success_repos: list[str] = field(default_factory=list)
    commented_repos: list[str] = field(default_factory=list)
    states: dict[str, RepoState] = field(default_factory=dict)

    def has_failures(self) -> bool:
        return bool(self.failed_repos)


class GitClient:
    def __init__(self, runtime: RuntimeGitSettings, base_dir: Path):
        self.runtime = runtime
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _run(self, *args: str, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def ensure_repo(self, repo: str) -> Path:
        repo_dir = self.base_dir / repo
        if not repo_dir.exists():
            clone = self._run("clone", self.runtime.with_credentials_url(repo), str(repo_dir))
            if clone.returncode != 0:
                detail = scrub_sensitive_text((clone.stderr or clone.stdout or "clone failed").strip())
                raise ReleaseTrackError(f"git clone 失败: {detail}")
        if not (repo_dir / ".git").exists():
            raise ReleaseTrackError("目录存在但不是 Git 仓库")
        return repo_dir

    def set_origin(self, repo: str, repo_dir: Path) -> None:
        set_url = self._run(
            "remote",
            "set-url",
            "origin",
            self.runtime.with_credentials_url(repo),
            cwd=repo_dir,
        )
        if set_url.returncode != 0:
            detail = scrub_sensitive_text((set_url.stderr or set_url.stdout or "set origin failed").strip())
            raise ReleaseTrackError(f"设置 origin 失败: {detail}")

    def rollback_merge_if_needed(self, repo_dir: Path) -> None:
        merge_head = self._run("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=repo_dir)
        if merge_head.returncode == 0:
            self._run("merge", "--abort", cwd=repo_dir)

    def ensure_clean_worktree(self, repo_dir: Path) -> None:
        st = self._run("status", "--porcelain", cwd=repo_dir)
        if st.returncode != 0:
            detail = scrub_sensitive_text((st.stderr or st.stdout or "status failed").strip())
            raise ReleaseTrackError(f"git status 失败: {detail}")
        if st.stdout.strip():
            raise ReleaseTrackError("工作区存在未提交改动")

    def fetch_all(self, repo_dir: Path) -> None:
        fr = self._run("fetch", "origin", "--tags", "--prune", cwd=repo_dir)
        if fr.returncode != 0:
            detail = scrub_sensitive_text((fr.stderr or fr.stdout or "fetch failed").strip())
            raise ReleaseTrackError(f"git fetch 失败: {detail}")

    def remote_branch_exists(self, repo_dir: Path, branch: str) -> bool:
        rc = self._run("ls-remote", "--exit-code", "--heads", "origin", branch, cwd=repo_dir)
        return rc.returncode == 0

    def checkout_and_pull(self, repo_dir: Path, branch: str) -> None:
        local_exists = self._run("show-ref", "--verify", f"refs/heads/{branch}", cwd=repo_dir)
        if local_exists.returncode == 0:
            co = self._run("checkout", branch, cwd=repo_dir)
        else:
            co = self._run("checkout", "-b", branch, f"origin/{branch}", cwd=repo_dir)
        if co.returncode != 0:
            detail = scrub_sensitive_text((co.stderr or co.stdout or "checkout failed").strip())
            raise ReleaseTrackError(f"切换分支失败 {branch}: {detail}")

        pull = self._run("pull", "--ff-only", "origin", branch, cwd=repo_dir)
        if pull.returncode != 0:
            detail = scrub_sensitive_text((pull.stderr or pull.stdout or "pull failed").strip())
            raise ReleaseTrackError(f"更新分支失败 {branch}: {detail}")

    def collect_pending(self, repo_dir: Path, release_branch: str, target_branch: str) -> tuple[int, str]:
        log = self._run("log", "--oneline", f"origin/{target_branch}..{release_branch}", cwd=repo_dir)
        if log.returncode != 0:
            detail = scrub_sensitive_text((log.stderr or log.stdout or "git log failed").strip())
            raise ReleaseTrackError(f"获取待合并提交失败: {detail}")
        pending_log = log.stdout.strip()
        lines = [line for line in pending_log.splitlines() if line.strip()]
        return len(lines), pending_log

    def local_trial_merge(self, repo_dir: Path, release_branch: str) -> None:
        mg = self._run("merge", "--no-commit", "--no-ff", release_branch, cwd=repo_dir)
        if mg.returncode != 0:
            self.rollback_merge_if_needed(repo_dir)
            detail = scrub_sensitive_text((mg.stderr or mg.stdout or "merge conflict").strip())
            raise ReleaseTrackError(f"本地 merge 冲突或失败: {detail}")

    def cleanup_trial_merge(self, repo_dir: Path, target_branch: str) -> None:
        abort = self._run("merge", "--abort", cwd=repo_dir)
        if abort.returncode != 0:
            self._run("reset", "--hard", f"origin/{target_branch}", cwd=repo_dir)

    def head_commit(self, repo_dir: Path) -> str:
        head = self._run("log", "-1", "--oneline", cwd=repo_dir)
        return head.stdout.strip()

    def reset_target_to_origin(self, repo_dir: Path, target_branch: str) -> None:
        self.checkout_and_pull(repo_dir, target_branch)
        rs = self._run("reset", "--hard", f"origin/{target_branch}", cwd=repo_dir)
        if rs.returncode != 0:
            detail = scrub_sensitive_text((rs.stderr or rs.stdout or "reset failed").strip())
            raise ReleaseTrackError(f"重置目标分支失败: {detail}")

    def remote_tag_exists(self, repo_dir: Path, tag_name: str) -> bool:
        ls = self._run("ls-remote", "--tags", "origin", f"refs/tags/{tag_name}", cwd=repo_dir)
        return ls.returncode == 0 and f"refs/tags/{tag_name}" in (ls.stdout or "")

    def create_and_push_tag(self, repo_dir: Path, tag_name: str, tag_message: str) -> str:
        local_tag = self._run("rev-parse", "-q", "--verify", f"refs/tags/{tag_name}", cwd=repo_dir)
        if local_tag.returncode == 0:
            self._run("tag", "-d", tag_name, cwd=repo_dir)

        create_tag = self._run("tag", "-a", tag_name, "-m", tag_message, cwd=repo_dir)
        if create_tag.returncode != 0:
            detail = scrub_sensitive_text((create_tag.stderr or create_tag.stdout or "create tag failed").strip())
            raise ReleaseTrackError(f"创建 tag 失败: {detail}")

        push = self._run("push", "origin", tag_name, cwd=repo_dir)
        if push.returncode != 0:
            detail = scrub_sensitive_text((push.stderr or push.stdout or "push tag failed").strip())
            raise ReleaseTrackError(f"push tag 失败: {detail}")
        return "tag 创建并推送成功"


class GitLabClient:
    def __init__(self, runtime: RuntimeGitSettings):
        if not runtime.git_pat and not (runtime.git_username and runtime.git_password):
            raise ReleaseTrackError("请先在管理员配置中设置 GitLab PAT 或用户名/密码")
        config = GitLabConfig(
            base_url=runtime.git_base_url,
            group=runtime.git_group,
            token=runtime.git_pat,
            username=runtime.git_username,
            password=runtime.git_password,
        )
        self.api = GitLabAPI(config)

    def create_mr(self, repo: str, source_branch: str, target_branch: str, title: str, description: str) -> tuple[str, int]:
        resp = self.api.create_mr(
            repo=repo,
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
        )
        if "message" in resp:
            raise ReleaseTrackError(f"创建 MR 失败: {resp.get('message')}")
        return str(resp.get("web_url") or ""), int(resp.get("iid") or 0)

    def merge_mr(self, repo: str, mr_iid: int, merge_message: str) -> str:
        resp = self.api.merge_mr(repo=repo, mr_iid=mr_iid, merge_commit_message=merge_message)
        if "message" in resp:
            raise ReleaseTrackError(f"自动合并 MR 失败: {resp.get('message')}")
        return str(resp.get("state") or "merged")

    def get_mr_state(self, repo: str, mr_iid: int) -> str:
        resp = self.api.get_mr(repo=repo, mr_iid=mr_iid)
        return str(resp.get("state") or "")


class ReleaseTrackService:
    def __init__(
        self,
        options: ReleaseTrackOptions,
        output: Callable[[str], None],
        event_callback: Callable[[dict], None] | None = None,
        approval_callback: Callable[[str], bool] | None = None,
    ):
        self.options = options
        self.output = output
        self.event_callback = event_callback
        self.approval_callback = approval_callback
        self.runtime = get_runtime_git_settings()
        if self.options.work_base_dir:
            self.base_dir = Path(self.options.work_base_dir)
        else:
            self.base_dir, _ = self.runtime.resolve_writable_work_base_path()
        self.git = GitClient(runtime=self.runtime, base_dir=self.base_dir)
        self.gitlab = GitLabClient(runtime=self.runtime)
        self.config = self._build_config()
        self.summary = ReleaseTrackSummary(
            tag_name=self.config.tag_name,
            merge_message=self.config.merge_message,
            tag_message=self.config.tag_message,
        )
        self.current_phase = "init"

    def run(self) -> ReleaseTrackSummary:
        self._set_phase("plan")
        plans = self._build_repo_plans()
        if not plans:
            raise ReleaseTrackError("该批次没有可追板仓库，请先确认“上线结束”标记")
        self._init_states(plans)
        self._emit("summary", summary=self.summary_to_dict())
        self._set_phase("precheck")
        self._phase_precheck(plans)
        self._print_stage_summary("本地预合并结果汇总")
        if not self.summary.ready_repos:
            self.output("没有可创建 MR 的仓库，流程结束。")
            self._set_phase("done")
            return self.summary
        if not self._confirm("是否创建并处理所有待处理仓库 MR？输入 yes 继续"):
            self.output("未确认 MR 阶段，流程结束。")
            self._set_phase("done")
            return self.summary

        self._set_phase("mr")
        self._phase_merge_request()
        self._print_stage_summary("MR 创建 / 合并结果汇总")
        if not self.summary.merged_repos:
            self.output("没有 MR 合并成功的仓库，不执行 tag。")
            self._set_phase("done")
            return self.summary

        self._set_phase("approval")
        self._approval_gate()
        self._set_phase("verify_mr")
        self._verify_mr_after_approval()
        if not self.summary.merged_repos:
            self.output("审批后无可打 tag 的仓库，流程结束。")
            self._set_phase("done")
            return self.summary

        if not self._confirm("是否继续为已合并成功仓库统一打 tag？输入 yes 继续"):
            self.output("未确认打 tag，流程结束。")
            self._set_phase("done")
            return self.summary
        self._set_phase("tag")
        self._phase_tag()
        self._print_stage_summary("Tag 结果汇总")
        self._set_phase("done")
        return self.summary

    def _build_config(self) -> ReleaseConfig:
        config = parse_release_config(self.options.config_text)
        tag_name = self.options.tag_name or config.tag_name
        merge_message = self.options.merge_message or config.merge_message
        tag_message = self.options.tag_message or config.tag_message
        if not tag_name:
            raise ReleaseTrackError("缺少 TAG_NAME（配置或参数）")
        if not merge_message:
            raise ReleaseTrackError("缺少 MERGE_MESSAGE（配置或参数）")
        if not tag_message:
            raise ReleaseTrackError("缺少 TAG_MESSAGE（配置或参数）")
        config.tag_name = tag_name
        config.merge_message = merge_message
        config.tag_message = tag_message
        # GitLab 需审批后才能合并，追板流程中固定禁用自动合并。
        config.auto_merge_mr = False
        config.force_tag = self.options.force_tag
        return config

    def _build_repo_plans(self) -> list[RepoPlan]:
        batch = ReleaseBatch.objects.filter(pk=self.options.batch_id).first()
        if not batch:
            raise ReleaseTrackError(f"批次不存在: {self.options.batch_id}")
        if batch.status != ReleaseBatch.Status.OPEN:
            raise ReleaseTrackError(f"仅支持 open 批次追板，当前状态: {batch.status}")

        selected_set = {
            str(code).strip()
            for code in (self.options.selected_projects or [])
            if str(code).strip()
        }
        items = (
            ReleaseItem.objects.select_related("project")
            .filter(batch=batch, rel_deployed=True)
            .order_by("project__project_code", "-updated_at", "-id")
        )

        plans: dict[str, RepoPlan] = {}
        for item in items:
            repo = (item.project.project_code or "").strip()
            if not repo:
                continue
            if selected_set and repo not in selected_set:
                continue
            target = self._target_branch_for_repo(repo) or self.options.default_target_branch
            plans[repo] = RepoPlan(
                repo=repo,
                release_branch=(item.release_branch or batch.release_branch).strip(),
                target_branch=target.strip(),
                source="batch",
            )

        if selected_set and not plans:
            raise ReleaseTrackError("勾选项目没有可执行仓库，请检查投产征集登记与上线结束标记")
        return list(plans.values())

    def _target_branch_for_repo(self, repo: str) -> str:
        for repo_cfg in self.config.repos:
            if repo_cfg.repo == repo:
                return repo_cfg.target_branch
        return ""

    def _init_states(self, plans: list[RepoPlan]) -> None:
        commented = [repo_cfg.repo for repo_cfg in self.config.repos if repo_cfg.commented]
        self.summary.commented_repos = commented
        for plan in plans:
            self.summary.states[plan.repo] = RepoState(
                repo=plan.repo,
                release_branch=plan.release_branch,
                target_branch=plan.target_branch,
                source=plan.source,
            )

    def _confirm(self, prompt: str) -> bool:
        if self.options.assume_yes:
            self.output(f"{prompt} [auto=yes]")
            self._emit("prompt", prompt=prompt, answer="yes", auto=True)
            return True
        answer = input(f"{prompt}: ").strip().lower()
        self._emit("prompt", prompt=prompt, answer=answer, auto=False)
        return answer == "yes"

    def _approval_gate(self) -> None:
        default_url = RELEASE_TRACK_APPROVAL_URL
        self.output("")
        self.output("MR 请求已发送，请管理员完成审批后确认继续：")
        self.output(default_url)
        self._emit("approval", url=default_url)
        if self.approval_callback is not None:
            approved = bool(self.approval_callback(default_url))
            self._emit("prompt", prompt="管理员审批闸口", answer="approved" if approved else "rejected", auto=False)
            if not approved:
                raise ReleaseTrackError("管理员未批准，流程中止")
            return
        if not self._confirm("管理员是否已完成审批？输入 yes 继续"):
            raise ReleaseTrackError("未确认管理员审批，流程中止")

    def _phase_precheck(self, plans: list[RepoPlan]) -> None:
        for plan in plans:
            state = self.summary.states[plan.repo]
            state.stage = "precheck"
            self.output("")
            self.output(f"处理仓库: {plan.repo}")
            self.output(f"投产分支: {plan.release_branch} -> 目标分支: {plan.target_branch}")
            try:
                repo_dir = self.git.ensure_repo(plan.repo)
                self.git.rollback_merge_if_needed(repo_dir)
                self.git.set_origin(plan.repo, repo_dir)
                self.git.ensure_clean_worktree(repo_dir)
                self.git.fetch_all(repo_dir)

                if not self.git.remote_branch_exists(repo_dir, plan.release_branch):
                    self._mark_failed(plan.repo, f"远端不存在投产分支 {plan.release_branch}")
                    continue
                if not self.git.remote_branch_exists(repo_dir, plan.target_branch):
                    self._mark_failed(plan.repo, f"远端不存在目标分支 {plan.target_branch}")
                    continue

                self.git.checkout_and_pull(repo_dir, plan.release_branch)
                self.git.checkout_and_pull(repo_dir, plan.target_branch)
                pending_count, pending_log = self.git.collect_pending(
                    repo_dir, plan.release_branch, plan.target_branch
                )
                state.pending_count = pending_count
                state.pending_log = pending_log
                if pending_count == 0:
                    self._mark_skipped(plan.repo, "无待合并提交")
                    continue

                self.git.local_trial_merge(repo_dir, plan.release_branch)
                state.merge_head = self.git.head_commit(repo_dir)
                self.git.cleanup_trial_merge(repo_dir, plan.target_branch)
                self._mark_ready(plan.repo, "本地预合并成功，待创建并合并 MR")
            except ReleaseTrackError as exc:
                self._mark_failed(plan.repo, scrub_sensitive_text(str(exc)))
            except subprocess.TimeoutExpired:
                self._mark_failed(plan.repo, "执行超时")

    def _phase_merge_request(self) -> None:
        to_create = list(self.summary.ready_repos)
        for repo in to_create:
            state = self.summary.states[repo]
            state.stage = "mr"
            try:
                title = f"{self.summary.tag_name}-{repo}"
                desc = (
                    f"repo={repo}; source={state.release_branch}; target={state.target_branch}; "
                    f"tag={self.summary.tag_name}; merge={self.summary.merge_message}; "
                    f"tagmsg={self.summary.tag_message}"
                )
                if self.options.dry_run:
                    state.mr_url = f"dry-run://{repo}/mr"
                    state.mr_iid = 1
                else:
                    mr_url, mr_iid = self.gitlab.create_mr(
                        repo=repo,
                        source_branch=state.release_branch,
                        target_branch=state.target_branch,
                        title=title,
                        description=desc,
                    )
                    state.mr_url = mr_url
                    state.mr_iid = mr_iid
                self._append_unique(repo, self.summary.mr_created_repos)

                if not self.config.auto_merge_mr:
                    self._mark_success(repo, "MR 已创建，未自动合并，跳过打 tag")
                    continue

                if self.options.dry_run:
                    state.mr_state = "merged"
                else:
                    state.mr_state = self.gitlab.merge_mr(repo, state.mr_iid, self.summary.merge_message)
                self._mark_merged(repo, "MR 创建后自动合并成功，可进入打 tag 阶段")
            except ReleaseTrackError as exc:
                self._mark_failed(repo, scrub_sensitive_text(str(exc)))

    def _verify_mr_after_approval(self) -> None:
        verified_merged: list[str] = []
        for repo in list(self.summary.merged_repos):
            state = self.summary.states[repo]
            if self.options.dry_run:
                mr_state = "merged"
            else:
                mr_state = self.gitlab.get_mr_state(repo, state.mr_iid)
            state.mr_state = mr_state
            if mr_state == "merged":
                verified_merged.append(repo)
            else:
                self._mark_failed(repo, f"审批后 MR 状态不是 merged: {mr_state or 'unknown'}")
        self.summary.merged_repos = verified_merged

    def _phase_tag(self) -> None:
        for repo in list(self.summary.merged_repos):
            state = self.summary.states[repo]
            state.stage = "tag"
            try:
                repo_dir = self.git.ensure_repo(repo)
                self.git.rollback_merge_if_needed(repo_dir)
                self.git.set_origin(repo, repo_dir)
                self.git.ensure_clean_worktree(repo_dir)
                self.git.fetch_all(repo_dir)
                self.git.reset_target_to_origin(repo_dir, state.target_branch)
                tag_exists = self.git.remote_tag_exists(repo_dir, self.summary.tag_name)
                if tag_exists:
                    state.tag_result = "远端 tag 已存在，未覆盖"
                    self._mark_success(repo, "MR 合并成功，远端 tag 已存在，未覆盖")
                    continue

                if self.options.dry_run:
                    state.tag_result = "dry-run tag success"
                else:
                    state.tag_result = self.git.create_and_push_tag(
                        repo_dir=repo_dir,
                        tag_name=self.summary.tag_name,
                        tag_message=self.summary.tag_message,
                    )
                self._append_unique(repo, self.summary.tag_success_repos)
                self._mark_success(repo, f"MR 合并成功，{state.tag_result}")
            except ReleaseTrackError as exc:
                state.tag_result = str(exc)
                self._mark_failed(repo, scrub_sensitive_text(str(exc)))

    def _mark_ready(self, repo: str, reason: str) -> None:
        state = self.summary.states[repo]
        state.status = "READY"
        state.reason = reason
        self._append_unique(repo, self.summary.ready_repos)
        self._remove(repo, self.summary.failed_repos)
        self._remove(repo, self.summary.skipped_repos)
        self._emit_repo_state(repo)

    def _mark_merged(self, repo: str, reason: str) -> None:
        state = self.summary.states[repo]
        state.status = "MERGED"
        state.reason = reason
        self._append_unique(repo, self.summary.merged_repos)
        self._remove(repo, self.summary.ready_repos)
        self._remove(repo, self.summary.failed_repos)
        self._remove(repo, self.summary.skipped_repos)
        self._emit_repo_state(repo)

    def _mark_success(self, repo: str, reason: str) -> None:
        state = self.summary.states[repo]
        state.status = "SUCCESS"
        state.reason = reason
        self._append_unique(repo, self.summary.success_repos)
        self._remove(repo, self.summary.failed_repos)
        self._remove(repo, self.summary.skipped_repos)
        self._emit_repo_state(repo)

    def _mark_skipped(self, repo: str, reason: str) -> None:
        state = self.summary.states[repo]
        state.status = "SKIPPED"
        state.reason = reason
        self._append_unique(repo, self.summary.skipped_repos)
        self._remove(repo, self.summary.failed_repos)
        self._remove(repo, self.summary.ready_repos)
        self._emit_repo_state(repo)

    def _mark_failed(self, repo: str, reason: str) -> None:
        state = self.summary.states[repo]
        state.status = "FAILED"
        state.reason = scrub_sensitive_text(reason)
        self._append_unique(repo, self.summary.failed_repos)
        self._remove(repo, self.summary.ready_repos)
        self._remove(repo, self.summary.merged_repos)
        self._emit_repo_state(repo)

    @staticmethod
    def _append_unique(value: str, target: list[str]) -> None:
        if value not in target:
            target.append(value)

    @staticmethod
    def _remove(value: str, target: list[str]) -> None:
        if value in target:
            target.remove(value)

    def _print_stage_summary(self, title: str) -> None:
        self.output("")
        self.output("=" * 60)
        self.output(title)
        self.output("=" * 60)
        for repo, state in self.summary.states.items():
            self.output(
                f"- {repo} [{state.status}] stage={state.stage} "
                f"release={state.release_branch} target={state.target_branch}"
            )
            if state.reason:
                self.output(f"  reason: {state.reason}")
            if state.pending_count:
                self.output(f"  pending: {state.pending_count}")
            if state.mr_url:
                self.output(f"  mr: {state.mr_url} (iid={state.mr_iid})")
            if state.tag_result:
                self.output(f"  tag: {state.tag_result}")
        self._emit("summary", title=title, summary=self.summary_to_dict())

    def summary_to_dict(self) -> dict:
        return {
            "tag_name": self.summary.tag_name,
            "merge_message": self.summary.merge_message,
            "tag_message": self.summary.tag_message,
            "current_phase": self.current_phase,
            "ready_repos": list(self.summary.ready_repos),
            "merged_repos": list(self.summary.merged_repos),
            "success_repos": list(self.summary.success_repos),
            "skipped_repos": list(self.summary.skipped_repos),
            "failed_repos": list(self.summary.failed_repos),
            "mr_created_repos": list(self.summary.mr_created_repos),
            "tag_success_repos": list(self.summary.tag_success_repos),
            "commented_repos": list(self.summary.commented_repos),
            "states": {repo: asdict(state) for repo, state in self.summary.states.items()},
        }

    def _set_phase(self, phase: str) -> None:
        self.current_phase = phase
        self._emit("phase", phase=phase)

    def _emit_repo_state(self, repo: str) -> None:
        state = self.summary.states.get(repo)
        if state is None:
            return
        self._emit("repo", repo=repo, state=asdict(state), summary=self.summary_to_dict())

    def _emit(self, event_type: str, **payload) -> None:
        if not self.event_callback:
            return
        self.event_callback({"event": event_type, **payload})


def build_config_text_from_file(config_file: str) -> str:
    path = Path(config_file)
    if not path.exists():
        raise ReleaseTrackError(f"配置文件不存在: {config_file}")
    return path.read_text(encoding="utf-8")

