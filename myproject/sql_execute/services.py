from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pymysql

from accounts.models import GitPlatformConfig

ProgressEvent = dict[str, str | int | None]


def parse_selected_files(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _split_sql_statements(content: str) -> list[str]:
    normalized = content.replace("\r\n", "\n")
    chunks = normalized.split(";")
    statements: list[str] = []
    for chunk in chunks:
        lines = []
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            lines.append(line)
        statement = "\n".join(lines).strip()
        if statement:
            statements.append(statement)
    return statements


def _pick_files_by_keyword(files: list[Path], keyword_set: set[str], consumed: set[Path]) -> list[Path]:
    result: list[Path] = []
    for file_path in files:
        if file_path in consumed:
            continue
        name = file_path.name.lower()
        if any(keyword in name for keyword in keyword_set):
            result.append(file_path)
            consumed.add(file_path)
    return result


def _build_execution_plan(folder_abs_path: Path, selected_files: list[str]) -> list[tuple[str, list[Path]]]:
    selected_abs: list[Path] = []
    for raw_path in selected_files:
        if not raw_path:
            continue
        src = Path(raw_path)
        candidate = src.resolve() if src.is_absolute() else (folder_abs_path / src).resolve()
        try:
            candidate.relative_to(folder_abs_path)
        except ValueError:
            continue
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".sql":
            selected_abs.append(candidate)

    selected_abs.sort(key=lambda p: p.name)
    consumed: set[Path] = set()
    backup = _pick_files_by_keyword(selected_abs, {"备份", "backup"}, consumed)
    ddl_1 = _pick_files_by_keyword(selected_abs, {"ddl"}, consumed)
    exec_1 = _pick_files_by_keyword(selected_abs, {"执行", "execute"}, consumed)
    rollback = _pick_files_by_keyword(selected_abs, {"回滚", "rollback"}, consumed)
    ddl_2 = _pick_files_by_keyword(selected_abs, {"ddl"}, consumed)
    exec_2 = _pick_files_by_keyword(selected_abs, {"执行", "execute"}, consumed)
    remaining = [path for path in selected_abs if path not in consumed]
    if remaining:
        exec_2.extend(remaining)
    return [
        ("备份", backup),
        ("DDL", ddl_1),
        ("执行", exec_1),
        ("回滚", rollback),
        ("DDL(二次)", ddl_2),
        ("执行(二次)", exec_2),
    ]


def _db_config_ready(config: GitPlatformConfig) -> tuple[bool, str]:
    if not config.sql_db_host.strip():
        return False, "MySQL Host 未配置"
    if not config.sql_db_name.strip():
        return False, "MySQL Database 未配置"
    if not config.sql_db_user.strip():
        return False, "MySQL User 未配置"
    if not config.sql_db_password.strip():
        return False, "MySQL Password 未配置"
    return True, ""


def _emit_progress(
    progress_callback: Callable[[ProgressEvent], None] | None,
    log_lines: list[str],
    *,
    log: str | None = None,
    tip: str | None = None,
) -> None:
    if log:
        log_lines.append(log.rstrip("\n"))
        line = log if log.endswith("\n") else f"{log}\n"
        if progress_callback:
            progress_callback({"log": line, "tip": tip})
    elif tip and progress_callback:
        progress_callback({"tip": tip})


def execute_sql_request(
    folder_path: str,
    selected_files_json: str,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> tuple[bool, str, str]:
    config = GitPlatformConfig.get_solo_safe()
    ok, reason = _db_config_ready(config)
    if not ok:
        return False, reason, reason

    folder_abs_path = Path(folder_path).resolve()
    if not folder_abs_path.exists() or not folder_abs_path.is_dir():
        return False, "SQL 目录不存在", "SQL 目录不存在"

    selected_files = parse_selected_files(selected_files_json)
    if not selected_files:
        return False, "未勾选 SQL 文件", "未勾选 SQL 文件"

    execution_plan = _build_execution_plan(folder_abs_path, selected_files)
    log_lines: list[str] = []
    total_files = sum(len(files) for _, files in execution_plan)
    done_files = 0

    connection = pymysql.connect(
        host=config.sql_db_host.strip(),
        port=int(config.sql_db_port or 3306),
        user=config.sql_db_user.strip(),
        password=config.sql_db_password,
        database=config.sql_db_name.strip(),
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            for phase_name, files in execution_plan:
                if not files:
                    msg = f"[{phase_name}] 无匹配 SQL，跳过"
                    _emit_progress(
                        progress_callback,
                        log_lines,
                        log=msg + "\n",
                        tip=f"阶段 {phase_name}：跳过",
                    )
                    continue
                _emit_progress(
                    progress_callback,
                    log_lines,
                    log=f"[{phase_name}] 开始，共 {len(files)} 个文件\n",
                    tip=f"阶段 {phase_name}：0/{len(files)}",
                )
                for sql_file in files:
                    done_files += 1
                    sql_content = sql_file.read_text(encoding="utf-8")
                    statements = _split_sql_statements(sql_content)
                    if not statements:
                        msg = f"[{phase_name}] {sql_file.name} 无可执行语句，跳过"
                        _emit_progress(
                            progress_callback,
                            log_lines,
                            log=msg + "\n",
                            tip=f"执行中 {done_files}/{total_files}：{sql_file.name}",
                        )
                        continue
                    _emit_progress(
                        progress_callback,
                        log_lines,
                        log=(
                            f"[{phase_name}] 开始执行 {sql_file.name}，"
                            f"语句数 {len(statements)}\n"
                        ),
                        tip=f"执行中 {done_files}/{total_files}：{sql_file.name}",
                    )
                    for idx, statement in enumerate(statements, start=1):
                        cursor.execute(statement)
                        _emit_progress(
                            progress_callback,
                            log_lines,
                            tip=(
                                f"执行中 {done_files}/{total_files}：{sql_file.name} "
                                f"语句 {idx}/{len(statements)}"
                            ),
                        )
                    connection.commit()
                    msg = f"[{phase_name}] 执行完成 {sql_file.name}"
                    _emit_progress(
                        progress_callback,
                        log_lines,
                        log=msg + "\n",
                        tip=f"执行中 {done_files}/{total_files}：{sql_file.name} 已完成",
                    )
        return True, "SQL 执行成功", "\n".join(log_lines)
    except Exception as exc:  # noqa: BLE001
        connection.rollback()
        err_msg = f"[ERROR] {exc}"
        log_lines.append(err_msg)
        if progress_callback:
            progress_callback({"log": err_msg + "\n", "tip": "执行失败"})
        return False, f"SQL 执行失败: {exc}", "\n".join(log_lines)
    finally:
        connection.close()
