from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from accounts.services.git_settings import get_runtime_git_settings, scrub_sensitive_text
from branch_create.config_parser import _map_project


@dataclass
class BranchTaskInput:
    source_type: str
    source_id: int
    project_code: str
    new_branch: str
    base_branch: str


@dataclass
class BranchTaskResult:
    source_type: str
    source_id: int
    project_code: str
    new_branch: str
    status: str
    message: str
    log: str


def normalize_project_code(project_code: str) -> str:
    mapped = _map_project(project_code)
    return mapped or project_code.strip()


class BranchExecutor:
    def __init__(self, work_base_dir: str | None = None):
        self.work_base_dir = Path(work_base_dir) if work_base_dir else None

    def _git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def execute(self, task: BranchTaskInput) -> BranchTaskResult:
        runtime = get_runtime_git_settings()
        resolved_base_dir, base_dir_mode = runtime.resolve_writable_work_base_path()
        work_base_dir = self.work_base_dir or resolved_base_dir
        project = normalize_project_code(task.project_code)
        project_dir = work_base_dir / project
        git_url = runtime.with_credentials_url(project)
        git_url_masked = runtime.masked_remote_url(project)
        logs: list[str] = []

        def add_log(msg: str) -> None:
            logs.append(scrub_sensitive_text(msg))

        def add_process_output(result: subprocess.CompletedProcess) -> None:
            out = scrub_sensitive_text((result.stdout or "").strip())
            err = scrub_sensitive_text((result.stderr or "").strip())
            if out:
                logs.append(out)
            if err:
                logs.append(err)

        def done(status: str, message: str) -> BranchTaskResult:
            return BranchTaskResult(
                task.source_type,
                task.source_id,
                project,
                task.new_branch,
                status,
                message,
                "\n".join(logs),
            )

        try:
            work_base_dir.mkdir(parents=True, exist_ok=True)
            add_log(f"work_base_dir={work_base_dir}")
            if not self.work_base_dir and base_dir_mode == "fallback":
                add_log(
                    "warn: 配置目录不可写，已自动回退到项目内目录 "
                    f"{work_base_dir}"
                )
            add_log(f"project_dir={project_dir}")
            add_log(f"git_url={git_url_masked}")
            add_log(f"auth_mode={runtime.preferred_auth()}")

            if not project_dir.exists():
                add_log("step: clone project")
                clone = self._git("clone", git_url, str(project_dir))
                if clone.returncode != 0:
                    add_process_output(clone)
                    return done("failed", "clone 失败")
                add_log("clone ok")
            elif not (project_dir / ".git").exists():
                return done("failed", f"目录存在但不是 Git 仓库: {project_dir}")

            add_log("step: set remote origin url")
            origin_set = self._git("remote", "set-url", "origin", git_url, cwd=project_dir)
            if origin_set.returncode != 0:
                add_process_output(origin_set)
                return done("failed", "设置远端地址失败")

            add_log("step: check status --porcelain")
            st = self._git("status", "--porcelain", cwd=project_dir)
            if st.returncode != 0:
                add_process_output(st)
                return done("failed", "status 检查失败")
            if st.stdout.strip():
                add_log(st.stdout.strip())
                return done("failed", "工作区有未提交改动")

            add_log("step: fetch origin --prune")
            fr = self._git("fetch", "origin", "--prune", cwd=project_dir)
            if fr.returncode != 0:
                add_process_output(fr)
                return done("failed", "git fetch 失败")

            add_log("step: local branch exists?")
            if self._git("show-ref", "--verify", f"refs/heads/{task.new_branch}", cwd=project_dir).returncode == 0:
                return done("skipped", "本地分支已存在")

            add_log("step: remote branch exists?")
            if self._git("ls-remote", "--exit-code", "--heads", "origin", task.new_branch, cwd=project_dir).returncode == 0:
                return done("skipped", "远程分支已存在")

            add_log(f"step: checkout base branch {task.base_branch}")
            if self._git("show-ref", "--verify", f"refs/heads/{task.base_branch}", cwd=project_dir).returncode == 0:
                co = self._git("checkout", task.base_branch, cwd=project_dir)
                if co.returncode != 0:
                    add_process_output(co)
                    return done("failed", f"切换基准分支失败: {task.base_branch}")
            else:
                co = self._git("checkout", "-b", task.base_branch, f"origin/{task.base_branch}", cwd=project_dir)
                if co.returncode != 0:
                    add_process_output(co)
                    return done("failed", f"基准分支不存在: {task.base_branch}")

            add_log("step: pull --ff-only origin base")
            pull = self._git("pull", "--ff-only", "origin", task.base_branch, cwd=project_dir)
            if pull.returncode != 0:
                add_process_output(pull)
                return done("failed", f"基准分支更新失败: {task.base_branch}")

            add_log(f"step: checkout -b {task.new_branch}")
            cb = self._git("checkout", "-b", task.new_branch, cwd=project_dir)
            if cb.returncode != 0:
                add_process_output(cb)
                return done("failed", "创建分支失败")

            add_log("step: push -u origin new branch")
            ps = self._git("push", "-u", "origin", task.new_branch, cwd=project_dir)
            if ps.returncode != 0:
                add_process_output(ps)
                return done("failed", "推送分支失败")

            add_log("done: success")
            return done("success", "创建成功")

        except subprocess.TimeoutExpired:
            add_log("error: timeout")
            return done("failed", "执行超时")
        except Exception as exc:
            add_log(f"error: {scrub_sensitive_text(str(exc))}")
            return done("failed", scrub_sensitive_text(str(exc)))
