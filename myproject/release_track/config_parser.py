"""
配置解析工具（复用原 bash 脚本的解析逻辑）
"""
import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReleaseRepoConfig:
    """投产追版 - 单个仓库配置"""

    repo: str
    release_branch: str
    target_branch: str
    commented: bool = False  # True 表示在配置中被注释掉了


@dataclass
class ReleaseConfig:
    """投产追版 - 全量配置"""

    tag_name: str
    merge_message: str
    tag_message: str
    repos: list = field(default_factory=list)

    # GitLab 连接配置（从环境变量读取）
    git_host: str = "gitlab.spdb.com"
    group: str = "zh-1087"
    base_dir: str = "/d/项目"
    gitlab_token: Optional[str] = None
    force_tag: bool = False
    auto_merge_mr: bool = True


@dataclass
class BranchTask:
    """新建分支 - 单条任务"""

    line_no: int
    new_branch: str
    raw_project: str
    mapped_project: str


@dataclass
class BuildPackageConfig:
    """做包 - 配置"""

    release_date: str
    version_tag: str
    change_no: str
    version_no: str
    commit_msg: str
    projects: list = field(default_factory=list)
    work_root: str = "/d/项目"
    repo_ssh: str = ""
    branch: str = "hobo"
    target_dir: str = ""


def parse_release_config(config_text: str) -> ReleaseConfig:
    """解析投产追版配置文件内容"""
    tag_name = ""
    merge_message = ""
    tag_message = ""
    repos = []

    for line_no, raw_line in enumerate(config_text.splitlines(), 1):
        line = raw_line.strip()

        # 跳过空行
        if not line:
            continue

        # 去掉行首 #（注释）
        commented = False
        if line.startswith("#"):
            line = line[1:].strip()
            commented = True
            if not line:
                continue

        # 变量赋值
        if line.startswith("TAG_NAME="):
            tag_name = line.split("=", 1)[1].strip()
            continue
        if line.startswith("MERGE_MESSAGE="):
            merge_message = line.split("=", 1)[1].strip()
            continue
        if line.startswith("TAG_MESSAGE="):
            tag_message = line.split("=", 1)[1].strip()
            continue

        # 跳过标题行
        if line.startswith("=") or "repo_name" in line.lower():
            continue

        # 解析仓库行：repo|release_branch|target_branch
        if "|" in line:
            parts = line.split("|")
            if len(parts) >= 3:
                repo = parts[0].strip()
                release_branch = parts[1].strip()
                target_branch = parts[2].strip()
                if repo and release_branch and target_branch:
                    repos.append(
                        ReleaseRepoConfig(
                            repo=repo,
                            release_branch=release_branch,
                            target_branch=target_branch,
                            commented=commented,
                        )
                    )

    return ReleaseConfig(
        tag_name=tag_name,
        merge_message=merge_message,
        tag_message=tag_message,
        repos=repos,
        gitlab_token=os.environ.get("GITLAB_TOKEN"),
    )


def parse_branch_config(config_text: str, base_branch: str = "master") -> list:
    """解析新建分支配置文件"""
    tasks = []

    # 标准项目列表（来自 branch_create.sh）
    standard_projects = [
        "hobo-customer-front",
        "hobo-element-front",
        "hobo-credit-front",
        "hobo-asset-front",
        "hobo-payment-front",
        "hobo-deposit-front",
        "hobo-pub-front",
        "hobo-work-front",
        "hobo-image-component",
        "hobo-factory-front",
        "hobo-flow-orch",
        "hobo-flow-config",
        "hobo-pub-flow",
        "hobo-deposit-flow",
        "hobo-customer-flow",
        "hobo-credit-flow",
        "hobo-element-flow",
        "hobo-asset-flow",
        "hobo-payment-flow",
    ]

    def normalize(name: str) -> str:
        name = name.strip().lower().replace(" ", "").replace("_", "-")
        return name

    def map_project(raw: str) -> Optional[str]:
        n = normalize(raw)
        # 直接匹配
        if n in standard_projects:
            return n
        # 自动加 hobo- 前缀
        if not n.startswith("hobo-"):
            candidate = f"hobo-{n}"
            if candidate in standard_projects:
                return candidate
        # 去掉 hobo- 再匹配
        suffix = n.replace("hobo-", "")
        for p in standard_projects:
            if p.replace("hobo-", "") == suffix:
                return p
        return None

    for line_no, raw_line in enumerate(config_text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        new_branch = parts[0].strip()
        raw_project = parts[1].strip()

        if not new_branch or not raw_project:
            continue

        mapped = map_project(raw_project)
        if mapped:
            tasks.append(
                BranchTask(
                    line_no=line_no,
                    new_branch=new_branch,
                    raw_project=raw_project,
                    mapped_project=mapped,
                )
            )

    return tasks
