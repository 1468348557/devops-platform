from __future__ import annotations

import base64
import json
import re
from typing import Any
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

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
from accounts.services.git_settings import get_runtime_git_settings, scrub_sensitive_text
from branch_create.models import ReleaseBatch

from .models import SqlExecutionRequest
from .services import ProgressEvent, execute_sql_request

_MAX_EXECUTION_LOG_CHARS = 100_000
_MAX_SQL_PREVIEW_CHARS = 200_000
_SQL_REPO_BRANCH = "rel执行且投产SQL"
_DEFAULT_DDL_KEYWORDS = "ddl"
_DEFAULT_BACKUP_KEYWORDS = "backup,bak,备份"
_DEFAULT_EXECUTE_KEYWORDS = "execute,执行"
_DEFAULT_ROLLBACK_KEYWORDS = "rollback,回滚"


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


_LOG_HEADER_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
_SQL_START_RE = re.compile(r"^开始执行\s+(\S+)\s*，\s*语句数\s+(\d+)\s*$")
_SQL_DONE_RE = re.compile(r"^执行完成\s+(\S+)\s*$")
_SQL_EMPTY_RE = re.compile(r"^(.+\.sql)\s+无可执行语句，跳过\s*$")


def _outcome_label(outcome: str) -> str:
    return {
        "success": "成功",
        "skip_phase": "阶段无脚本",
        "skip_empty": "文件无可执行语句",
        "running": "执行中",
        "partial": "未完成",
        "error": "失败",
        "info": "信息",
    }.get(outcome, outcome or "-")


def _g_style_fields_for_file_entry(entry: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = [
        {"key": "phase", "label": "阶段", "value": str(entry.get("phase") or "-")},
    ]
    if entry.get("file_name"):
        rows.append({"key": "file", "label": "文件", "value": str(entry["file_name"])})
    rows.append({"key": "outcome", "label": "结果", "value": str(entry.get("outcome_label") or "-")})
    if entry.get("statement_count") is not None:
        rows.append(
            {
                "key": "statements",
                "label": "语句数",
                "value": str(entry["statement_count"]),
            }
        )
    log_text = (entry.get("log") or "").strip()
    if log_text:
        log_label = "执行记录（含查询输出）" if "[结果]" in log_text else "日志"
        rows.append({"key": "log", "label": log_label, "value": log_text})
    return rows


def _parse_sql_execution_log(execution_log: str) -> dict[str, Any]:
    """
    从 execution_log 纯文本中解析出按文件/阶段分段的结构（不落库，仅用于展示）。
    日志格式由 sql_execute.services.execute_sql_request 产生。
    """
    text = (execution_log or "").replace("\r\n", "\n")
    if not text.strip():
        return {"files": [], "orphan_log": ""}

    files: list[dict[str, Any]] = []
    orphan_lines: list[str] = []
    current: dict[str, Any] | None = None

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        log_body = "\n".join(current["lines"]).strip()
        oc = str(current.get("outcome") or "partial")
        if oc == "running":
            oc = "partial"
        files.append(
            {
                "phase": current.get("phase"),
                "file_name": current.get("file_name"),
                "outcome": oc,
                "outcome_label": _outcome_label(oc),
                "statement_count": current.get("statement_count"),
                "log": log_body,
            }
        )
        current = None

    def append_to_current_or_orphan(line: str) -> None:
        if current is not None:
            current["lines"].append(line)
        elif line.strip():
            orphan_lines.append(line)

    for line in text.split("\n"):
        m = _LOG_HEADER_RE.match(line)
        if not m:
            append_to_current_or_orphan(line)
            continue

        tag = m.group(1).strip()
        rest = m.group(2).strip()

        if tag in {"ERROR", "WARN"} or "ERROR" in tag or "WARN" in tag:
            append_to_current_or_orphan(line)
            continue

        if rest == "无匹配脚本，跳过" or rest.endswith("无匹配脚本，跳过"):
            flush_current()
            files.append(
                {
                    "phase": tag,
                    "file_name": None,
                    "outcome": "skip_phase",
                    "outcome_label": _outcome_label("skip_phase"),
                    "statement_count": None,
                    "log": line.strip(),
                }
            )
            continue

        m_empty = _SQL_EMPTY_RE.match(rest)
        if m_empty:
            flush_current()
            files.append(
                {
                    "phase": tag,
                    "file_name": m_empty.group(1).strip(),
                    "outcome": "skip_empty",
                    "outcome_label": _outcome_label("skip_empty"),
                    "statement_count": None,
                    "log": line.strip(),
                }
            )
            continue

        m_start = _SQL_START_RE.match(rest)
        if m_start:
            flush_current()
            current = {
                "phase": tag,
                "file_name": m_start.group(1),
                "outcome": "running",
                "statement_count": int(m_start.group(2)),
                "lines": [line],
            }
            continue

        m_done = _SQL_DONE_RE.match(rest)
        if m_done:
            fn = m_done.group(1)
            if current and current.get("file_name") == fn:
                current["lines"].append(line)
                current["outcome"] = "success"
                flush_current()
            else:
                flush_current()
                files.append(
                    {
                        "phase": tag,
                        "file_name": fn,
                        "outcome": "success",
                        "outcome_label": _outcome_label("success"),
                        "statement_count": None,
                        "log": line.strip(),
                    }
                )
            continue

        if current is not None:
            current["lines"].append(line)
        elif line.strip():
            files.append(
                {
                    "phase": tag,
                    "file_name": None,
                    "outcome": "info",
                    "outcome_label": _outcome_label("info"),
                    "statement_count": None,
                    "log": line.strip(),
                }
            )

    flush_current()
    orphan_log = "\n".join(orphan_lines).strip()
    return {"files": files, "orphan_log": orphan_log}


def _build_execution_parsed(row: SqlExecutionRequest) -> dict[str, Any]:
    parsed = _parse_sql_execution_log(row.execution_log or "")
    files_out: list[dict[str, Any]] = []
    for idx, f in enumerate(parsed.get("files") or []):
        entry = {**f, "id": str(idx)}
        entry["g_fields"] = _g_style_fields_for_file_entry(entry)
        files_out.append(entry)
    return {
        "summary": {"overall": row.get_status_display()},
        "files": files_out,
        "orphan_log": parsed.get("orphan_log") or "",
    }


def _can_access(user) -> bool:
    return can_access_menu(user, "sql_execute")


def _can_apply(user) -> bool:
    return can_do_action(user, "sql_request_apply")


def _can_approve(user) -> bool:
    return can_do_action(user, "sql_request_approve")


def _can_auto_approve(user) -> bool:
    return can_do_action(user, "sql_request_auto_approve")


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
    return _resolve_repo_path(Path(raw).resolve())


def _resolve_repo_path(configured_path: Path) -> Path:
    if (configured_path / ".git").exists():
        return configured_path
    if not configured_path.exists() or not configured_path.is_dir():
        return configured_path
    try:
        git_children = [
            child
            for child in sorted(configured_path.iterdir(), key=lambda p: p.name)
            if child.is_dir() and (child / ".git").exists()
        ]
    except OSError:
        return configured_path
    if len(git_children) == 1:
        return git_children[0]
    return configured_path


def _infer_repo_name_from_clone_url(clone_url: str) -> str:
    path = urlsplit((clone_url or "").strip()).path.strip("/")
    if not path:
        return "sql-repo"
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    cleaned = name.strip() or "sql-repo"
    return cleaned


def _is_empty_dir(path: Path) -> bool:
    try:
        return next(path.iterdir(), None) is None
    except OSError:
        return False


def _pick_clone_target(configured_path: Path, clone_url: str) -> tuple[Path | None, str | None]:
    if not configured_path.exists():
        return configured_path, None
    if not configured_path.is_dir():
        return None, "SQL 仓库路径不是目录"
    if _is_empty_dir(configured_path):
        return configured_path, None
    candidate = configured_path / _infer_repo_name_from_clone_url(clone_url)
    if candidate.exists() and not (candidate / ".git").exists():
        return None, "目录存在但不是 Git 仓库"
    return candidate, None


def _git_auth_config_args() -> list[str]:
    runtime = get_runtime_git_settings()
    username = ""
    secret = ""
    if runtime.git_pat:
        username = "oauth2"
        secret = runtime.git_pat
    elif runtime.git_username and runtime.git_password:
        username = runtime.git_username
        secret = runtime.git_password
    if not username or not secret:
        return []
    token_raw = f"{username}:{secret}".encode("utf-8")
    token = base64.b64encode(token_raw).decode("ascii")
    return ["-c", f"http.extraHeader=Authorization: Basic {token}"]


def _git_run(repo_path: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *_git_auth_config_args(), "-C", str(repo_path), *args],
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
        detail = scrub_sensitive_text(fetch_res.stderr.strip() or fetch_res.stdout.strip())
        return False, detail or f"拉取分支 {_SQL_REPO_BRANCH} 失败"

    checkout_res = _git_run(repo_path, "checkout", _SQL_REPO_BRANCH)
    if checkout_res.returncode != 0:
        # 某些异常仓库（例如历史 clone 失败后遗留的 .git）可能没有 origin/<branch> 引用，
        # 但 fetch 后仍会有 FETCH_HEAD，可直接从 FETCH_HEAD 建立本地分支。
        create_from_fetch_head = _git_run(
            repo_path,
            "checkout",
            "-B",
            _SQL_REPO_BRANCH,
            "FETCH_HEAD",
        )
        if create_from_fetch_head.returncode != 0:
            create_from_origin = _git_run(
                repo_path,
                "checkout",
                "-b",
                _SQL_REPO_BRANCH,
                f"origin/{_SQL_REPO_BRANCH}",
            )
            if create_from_origin.returncode != 0:
                detail = scrub_sensitive_text(
                    create_from_origin.stderr.strip()
                    or create_from_origin.stdout.strip()
                    or create_from_fetch_head.stderr.strip()
                    or create_from_fetch_head.stdout.strip()
                )
                return False, detail or f"切换分支 {_SQL_REPO_BRANCH} 失败"

    if not pull_remote:
        return True, ""
    pull_res = _git_run(repo_path, "pull", "--ff-only", "origin", _SQL_REPO_BRANCH)
    if pull_res.returncode != 0:
        detail = scrub_sensitive_text(pull_res.stderr.strip() or pull_res.stdout.strip())
        return False, detail or "同步失败"
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
    """在投产征集日期中选取「大于等于 today」的最早一天（YYYY-MM-DD），无则空串。"""
    on_or_after = [value for value in release_dates if value and value >= today]
    if not on_or_after:
        return ""
    return str(min(on_or_after))


def _serialize_request(row: SqlExecutionRequest) -> dict:
    selected_files = _request_selected_files(row)
    selected_files_display = _request_selected_files_display(row, selected_files)
    selected_file_items = [
        {"raw": raw, "display": display}
        for raw, display in zip(selected_files, selected_files_display)
    ]
    return {
        "id": row.id,
        "release_date": str(row.release_date),
        "folder_path": row.folder_path,
        "selected_files": selected_files,
        "selected_files_display": selected_files_display,
        "selected_file_items": selected_file_items,
        "status": row.status,
        "status_label": row.get_status_display(),
        "execution_result": row.execution_result,
        "execution_tip": row.execution_tip,
        "execution_log": row.execution_log,
        "requested_by": row.requested_by.username,
        "requested_by_id": row.requested_by_id,
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


def _request_selected_files_display(row: SqlExecutionRequest, selected_files: list[str]) -> list[str]:
    folder = Path((row.folder_path or "").strip()).resolve()
    folder_name = folder.name or "-"
    display: list[str] = []
    for raw in selected_files:
        file_name = Path(raw).name or raw
        display.append(f"{folder_name}/{file_name}")
    return display


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


def _safe_read_sql_file_from_repo(repo_path: Path, folder: str, file_name: str) -> tuple[bool, str]:
    normalized_folder = (folder or "").strip()
    normalized_name = (file_name or "").strip()
    if not normalized_folder:
        return False, "folder 不能为空"
    if not normalized_name:
        return False, "file_name 不能为空"
    if not normalized_name.lower().endswith(".sql"):
        return False, "仅支持查看 .sql 文件"
    if "/" in normalized_name or "\\" in normalized_name:
        return False, "file_name 非法"

    folder_abs = (repo_path / normalized_folder).resolve()
    try:
        folder_abs.relative_to(repo_path)
    except ValueError:
        return False, "目录非法"
    if not folder_abs.exists() or not folder_abs.is_dir():
        return False, "SQL 目录不存在"

    valid_files = set(_list_sql_files(repo_path, normalized_folder))
    if normalized_name not in valid_files:
        return False, "SQL 文件不存在"

    target = (folder_abs / normalized_name).resolve()
    try:
        target.relative_to(folder_abs)
    except ValueError:
        return False, "文件路径非法"
    if not target.exists() or not target.is_file():
        return False, "SQL 文件不存在"

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"读取 SQL 文件失败：{exc}"
    if len(content) > _MAX_SQL_PREVIEW_CHARS:
        content = content[:_MAX_SQL_PREVIEW_CHARS] + "\n\n-- [预览已截断]"
    return True, content


def _first_non_empty_sql_line(content: str) -> str:
    normalized = content.lstrip("\ufeff")
    in_block_comment = False
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
                line = line.split("*/", 1)[1].strip()
                if not line:
                    continue
            else:
                continue
        while line.startswith("/*"):
            if "*/" in line:
                line = line.split("*/", 1)[1].strip()
                if not line:
                    break
            else:
                in_block_comment = True
                line = ""
                break
        if not line:
            continue
        if line.startswith("--") or line.startswith("#"):
            continue
        return line
    return ""


def _parse_keywords(raw: str, default_raw: str) -> list[str]:
    source = (raw or "").strip() or default_raw
    parts = [part.strip().lower() for part in source.replace("，", ",").split(",")]
    return [part for part in parts if part]


def _matches_any_keyword(file_name: str, keywords: list[str]) -> bool:
    lowered = file_name.lower()
    return any(keyword in lowered for keyword in keywords)


def _keyword_display(keywords: list[str]) -> str:
    return ",".join(keywords) if keywords else "-"


def _normalize_phase_name(raw: str) -> str:
    value = (raw or "").strip().lower()
    mapping = {
        "backup": "backup",
        "备份": "backup",
        "ddl": "ddl",
        "execute": "execute",
        "执行": "execute",
        "rollback": "rollback",
        "回滚": "rollback",
    }
    return mapping.get(value, "")


def _parse_submit_phase_order_rules(raw: str) -> list[list[str]]:
    default_rule = ["backup", "ddl", "execute", "rollback"]
    raw_text = (raw or "").replace("，", ",")
    candidates = [item.strip() for item in raw_text.replace("\n", ";").split(";") if item.strip()]
    if not candidates:
        candidates = [",".join(default_rule)]

    rules: list[list[str]] = []
    for candidate in candidates:
        normalized: list[str] = []
        for part in candidate.split(","):
            phase = _normalize_phase_name(part)
            if not phase:
                continue
            normalized.append(phase)
        if not normalized:
            continue
        rules.append(normalized)

    if not rules:
        return [default_rule]
    return rules


def _match_sql_phase(
    file_name: str,
    ddl_keywords: list[str],
    backup_keywords: list[str],
    execute_keywords: list[str],
    rollback_keywords: list[str],
) -> str | None:
    matches = {
        "ddl": _matches_any_keyword(file_name, ddl_keywords),
        "backup": _matches_any_keyword(file_name, backup_keywords),
        "execute": _matches_any_keyword(file_name, execute_keywords),
        "rollback": _matches_any_keyword(file_name, rollback_keywords),
    }
    hit_phases = [phase for phase, hit in matches.items() if hit]
    if len(hit_phases) == 1:
        return hit_phases[0]
    return None


def _machine_review_sql_files(
    selected_paths: list[Path],
    expected_db_name: str,
    ddl_keywords: list[str],
    backup_keywords: list[str],
    execute_keywords: list[str],
    rollback_keywords: list[str],
) -> tuple[bool, str]:
    """
    重写后的机器审批规则：
    1) 文件必须是 UTF-8 且非空；
    2) 文件名必须仅能匹配一种脚本类型（DDL/备份/执行/回滚，来自管理员关键字）；
    3) 执行与回滚类型至少各 1 个（DDL/备份可选）；
    4) 若配置了数据库名，则首条有效 SQL 必须为 `use <db>;`（允许中文分号与行尾注释）；
    5) 若 DDL 脚本中出现建表语句（create [temporary] table），则每一处均须为
       create table if not exists（无建表语句则不校验此项）；
       若回滚脚本中出现删表语句（drop [temporary] table），则每一处均须为
       drop table if exists（无删表语句则不校验此项）。
    """
    normalized_db_name = (expected_db_name or "").strip()
    phase_hits = {"ddl": 0, "backup": 0, "execute": 0, "rollback": 0}
    create_table_any_re = re.compile(
        r"\bcreate\s+(?:temporary\s+)?table\b",
        re.IGNORECASE,
    )
    create_table_bad_re = re.compile(
        r"\bcreate\s+(?:temporary\s+)?table\s+(?!if\s+not\s+exists\b)",
        re.IGNORECASE,
    )
    drop_table_any_re = re.compile(
        r"\bdrop\s+(?:temporary\s+)?table\b",
        re.IGNORECASE,
    )
    drop_table_bad_re = re.compile(
        r"\bdrop\s+(?:temporary\s+)?table\s+(?!if\s+exists\b)",
        re.IGNORECASE,
    )
    use_db_re = (
        re.compile(
            rf"^use\s+{re.escape(normalized_db_name)}\s*(?:;|；)\s*(?:--.*|#.*)?$",
            re.IGNORECASE,
        )
        if normalized_db_name
        else None
    )

    for sql_path in selected_paths:
        file_name = sql_path.name
        matched_phase = _match_sql_phase(
            file_name,
            ddl_keywords,
            backup_keywords,
            execute_keywords,
            rollback_keywords,
        )
        if not matched_phase:
            return (
                False,
                "机器审批不通过："
                f"文件名 {file_name} 无法匹配脚本类型或命中多个类型关键字（DDL:{_keyword_display(ddl_keywords)}；"
                f"备份:{_keyword_display(backup_keywords)}；执行:{_keyword_display(execute_keywords)}；"
                f"回滚:{_keyword_display(rollback_keywords)}）",
            )
        phase_hits[matched_phase] += 1

        try:
            raw = sql_path.read_bytes()
        except OSError as exc:
            return False, f"机器审批不通过：读取文件失败 {file_name}（{exc}）"
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return False, f"机器审批不通过：{file_name} 不是 UTF-8 编码"

        if not content.strip():
            return False, f"机器审批不通过：{file_name} 内容为空"

        if use_db_re:
            first_line = _first_non_empty_sql_line(content)
            if not use_db_re.match(first_line):
                return False, f"机器审批不通过：{file_name} 首行必须是 use {normalized_db_name};"

        if matched_phase == "ddl" and create_table_any_re.search(content):
            if create_table_bad_re.search(content):
                return False, (
                    f"机器审批不通过：DDL 脚本 {file_name} "
                    "中含建表语句时须全部使用 create table if not exists（或可带 temporary）"
                )
        if matched_phase == "rollback" and drop_table_any_re.search(content):
            if drop_table_bad_re.search(content):
                return False, (
                    f"机器审批不通过：回滚脚本 {file_name} "
                    "中含删表语句时须全部使用 drop table if exists（或可带 temporary）"
                )

    if phase_hits["execute"] == 0:
        return False, f"机器审批不通过：缺少执行脚本（关键字：{_keyword_display(execute_keywords)}）"
    if phase_hits["rollback"] == 0:
        return False, f"机器审批不通过：缺少回滚脚本（关键字：{_keyword_display(rollback_keywords)}）"
    return True, ""


def _is_request_order_allowed_by_rules(
    selected_files: list[str],
    config: GitPlatformConfig,
) -> tuple[bool, str]:
    if not selected_files:
        return False, "未勾选 SQL 文件"
    ddl_keywords = _parse_keywords(getattr(config, "sql_keyword_ddl", ""), _DEFAULT_DDL_KEYWORDS)
    backup_keywords = _parse_keywords(getattr(config, "sql_keyword_backup", ""), _DEFAULT_BACKUP_KEYWORDS)
    execute_keywords = _parse_keywords(
        getattr(config, "sql_keyword_execute", ""),
        _DEFAULT_EXECUTE_KEYWORDS,
    )
    rollback_keywords = _parse_keywords(
        getattr(config, "sql_keyword_rollback", ""),
        _DEFAULT_ROLLBACK_KEYWORDS,
    )
    phase_order_rules = _parse_submit_phase_order_rules(
        getattr(config, "sql_auto_approve_order", ""),
    )

    matched_phases: list[str] = []
    for raw_path in selected_files:
        name = Path(raw_path).name
        phase = _match_sql_phase(name, ddl_keywords, backup_keywords, execute_keywords, rollback_keywords)
        if not phase:
            return False, f"文件 {name} 无法识别脚本类型"
        matched_phases.append(phase)

    for phase_order in phase_order_rules:
        if matched_phases == phase_order:
            return True, ""

    display_rules = " | ".join("->".join(rule) for rule in phase_order_rules)
    return (
        False,
        f"文件顺序不符合研发提交流程规则（当前顺序: {' -> '.join(matched_phases)}；允许规则: {display_rules}）",
    )


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
            "can_auto_approve": _can_auto_approve(request.user),
            "current_user_id": request.user.id,
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
    config = GitPlatformConfig.get_solo_safe()
    configured_raw = (config.sql_repo_path or "").strip()
    if not configured_raw:
        return JsonResponse({"success": False, "error": "请先在管理员配置 SQL 仓库路径"}, status=400)
    configured_repo_path = Path(configured_raw).resolve()
    repo_path = _resolve_repo_path(configured_repo_path)
    clone_url = (config.sql_repo_clone_url or "").strip()
    if not repo_path.exists() or (repo_path.exists() and not (repo_path / ".git").exists()):
        if not clone_url:
            return JsonResponse({"success": False, "error": "仓库路径不存在，且未配置 Clone URL"}, status=400)
        target_path, target_error = _pick_clone_target(configured_repo_path, clone_url)
        if target_error:
            return JsonResponse({"success": False, "error": target_error}, status=400)
        if target_path is None:
            return JsonResponse({"success": False, "error": "无法确定 SQL 仓库 clone 目标路径"}, status=400)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        git_cmd = [
            "git",
            *_git_auth_config_args(),
            "clone",
            "--branch",
            _SQL_REPO_BRANCH,
            "--single-branch",
            clone_url,
            str(target_path),
        ]
        clone_result = subprocess.run(
            git_cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if clone_result.returncode != 0:
            detail = scrub_sensitive_text(clone_result.stderr.strip() or clone_result.stdout.strip())
            return JsonResponse(
                {"success": False, "error": detail or "仓库克隆失败"},
                status=400,
            )
        repo_path = _resolve_repo_path(configured_repo_path)
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
@require_http_methods(["GET"])
def sql_repo_file_preview_api(request):
    if not _can_apply(request.user):
        return JsonResponse({"success": False, "error": "无申请执行权限"}, status=403)
    folder = (request.GET.get("folder") or "").strip()
    file_name = (request.GET.get("file_name") or "").strip()
    repo_path = _get_repo_path()
    if not repo_path:
        return JsonResponse({"success": False, "error": "请先在管理员配置 SQL 仓库路径"}, status=400)
    ok, msg = _ensure_sql_repo_branch(repo_path, pull_remote=False)
    if not ok:
        return JsonResponse({"success": False, "error": msg}, status=400)
    read_ok, result = _safe_read_sql_file_from_repo(repo_path, folder, file_name)
    if not read_ok:
        return JsonResponse({"success": False, "error": result}, status=400)
    return JsonResponse(
        {
            "success": True,
            "folder": folder,
            "file_name": file_name,
            "content": result,
        }
    )


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
            if not cleaned or cleaned not in valid_file_set:
                continue
            normalized_selected.append(str((folder_abs / cleaned).resolve()))
    else:
        normalized_selected = [str((folder_abs / file_name).resolve()) for file_name in valid_files]
    if not normalized_selected:
        return JsonResponse({"success": False, "error": "未找到有效 SQL 文件"}, status=400)

    selected_paths = [Path(value) for value in normalized_selected]
    config = GitPlatformConfig.get_solo_safe()
    sql_db_name = (config.sql_db_name or "").strip()
    ddl_keywords = _parse_keywords(getattr(config, "sql_keyword_ddl", ""), _DEFAULT_DDL_KEYWORDS)
    backup_keywords = _parse_keywords(getattr(config, "sql_keyword_backup", ""), _DEFAULT_BACKUP_KEYWORDS)
    execute_keywords = _parse_keywords(
        getattr(config, "sql_keyword_execute", ""),
        _DEFAULT_EXECUTE_KEYWORDS,
    )
    rollback_keywords = _parse_keywords(
        getattr(config, "sql_keyword_rollback", ""),
        _DEFAULT_ROLLBACK_KEYWORDS,
    )
    review_ok, review_msg = _machine_review_sql_files(
        selected_paths,
        sql_db_name,
        ddl_keywords,
        backup_keywords,
        execute_keywords,
        rollback_keywords,
    )
    if not review_ok:
        return JsonResponse({"success": False, "error": review_msg}, status=400)
    order_ok, order_msg = _is_request_order_allowed_by_rules(normalized_selected, config)
    if not order_ok:
        return JsonResponse({"success": False, "error": f"机器审批不通过：{order_msg}"}, status=400)

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
    return JsonResponse(
        {
            "success": True,
            "request": _serialize_request(row),
            "execution_parsed": _build_execution_parsed(row),
        }
    )


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
    request_id_raw = (request.POST.get("request_id") or "").strip()
    action = (request.POST.get("action") or "").strip().lower()
    if not request_id_raw.isdigit():
        return JsonResponse({"success": False, "error": "request_id 非法"}, status=400)
    if action not in {"approve", "auto_approve", "reject", "withdraw"}:
        return JsonResponse({"success": False, "error": "action 非法"}, status=400)
    if action in {"approve", "reject"} and not _can_approve(request.user):
        return JsonResponse({"success": False, "error": "无审批权限"}, status=403)
    if action == "auto_approve" and not _can_auto_approve(request.user):
        return JsonResponse({"success": False, "error": "无自动审批权限"}, status=403)
    if action == "withdraw" and not _can_apply(request.user):
        return JsonResponse({"success": False, "error": "无申请执行权限"}, status=403)

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

        if action == "withdraw":
            if row.requested_by_id != request.user.id:
                return JsonResponse({"success": False, "error": "仅可撤回自己的申请"}, status=403)
            row.status = SqlExecutionRequest.Status.REJECTED
            row.execution_result = "申请撤回"
            row.execution_tip = ""
            row.execution_log = ""
            row.approved_by = None
            row.approved_at = None
            row.save(
                update_fields=[
                    "status",
                    "execution_result",
                    "execution_tip",
                    "execution_log",
                    "approved_by",
                    "approved_at",
                    "updated_at",
                ]
            )
            return JsonResponse({"success": True, "status": row.status})

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
        row.execution_result = "执行中" if action == "approve" else "自动审批执行中"
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


@login_required
@require_http_methods(["POST"])
def sql_request_auto_approve_all_api(request):
    if not _can_auto_approve(request.user):
        return JsonResponse({"success": False, "error": "无自动审批权限"}, status=403)

    pending_qs = SqlExecutionRequest.objects.filter(status=SqlExecutionRequest.Status.PENDING)
    if not can_do_action(request.user, "sql_request_edit_others"):
        pending_qs = apply_data_scope(
            pending_qs,
            request.user,
            scope_key="sql_requests",
            owner_field="requested_by",
        )
    pending_rows = list(pending_qs.order_by("id"))
    if not pending_rows:
        return JsonResponse({"success": True, "count": 0, "request_ids": [], "skipped": 0})

    eligible_ids: list[int] = []
    skipped_details: list[dict[str, str | int]] = []
    for row in pending_rows:
        eligible_ids.append(row.id)
    if not eligible_ids:
        return JsonResponse(
            {
                "success": True,
                "count": 0,
                "request_ids": [],
                "skipped": len(skipped_details),
                "skipped_details": skipped_details[:20],
            }
        )

    started_ids: list[int] = []
    with transaction.atomic():
        rows = (
            SqlExecutionRequest.objects.select_for_update()
            .filter(id__in=eligible_ids, status=SqlExecutionRequest.Status.PENDING)
            .order_by("id")
        )
        now = timezone.now()
        for row in rows:
            row.approved_by = request.user
            row.approved_at = now
            row.status = SqlExecutionRequest.Status.RUNNING
            row.execution_result = "自动审批执行中"
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
            started_ids.append(row.id)

    for request_id in started_ids:
        transaction.on_commit(
            lambda rid=request_id: _spawn_detached_command("run_sql_execute_request", str(rid))
        )
    return JsonResponse(
        {
            "success": True,
            "count": len(started_ids),
            "request_ids": started_ids,
            "skipped": len(skipped_details),
            "skipped_details": skipped_details[:20],
        }
    )
