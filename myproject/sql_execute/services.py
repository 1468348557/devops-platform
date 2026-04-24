from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pymysql
from pymysql.cursors import Cursor

from accounts.models import GitPlatformConfig

ProgressEvent = dict[str, str | int | None]
_DEFAULT_DDL_KEYWORDS = "ddl"
# 单次结果写入 execution_log 的上限，避免把超大结果集打爆数据库
_MAX_QUERY_RESULT_ROWS = 200
_MAX_QUERY_CELL_LEN = 4000
_MAX_QUERY_RESULT_LOG_CHARS = 60_000
_DEFAULT_BACKUP_KEYWORDS = "backup,bak,备份"
_DEFAULT_EXECUTE_KEYWORDS = "execute,执行"
_DEFAULT_ROLLBACK_KEYWORDS = "rollback,回滚"


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


def _build_execution_files(folder_abs_path: Path, selected_files: list[str]) -> list[Path]:
    selected_abs: list[Path] = []
    seen: set[Path] = set()
    for raw_path in selected_files:
        if not raw_path:
            continue
        src = Path(raw_path)
        candidate = src.resolve() if src.is_absolute() else (folder_abs_path / src).resolve()
        try:
            candidate.relative_to(folder_abs_path)
        except ValueError:
            continue
        if not candidate.exists() or not candidate.is_file() or candidate.suffix.lower() != ".sql":
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        selected_abs.append(candidate)
    return selected_abs


def _parse_keywords(raw: str, default_raw: str) -> list[str]:
    source = (raw or "").strip() or default_raw
    parts = [part.strip().lower() for part in source.replace("，", ",").split(",")]
    return [part for part in parts if part]


def _phase_for_sql_file(
    file_path: Path,
    *,
    ddl_keywords: list[str],
    backup_keywords: list[str],
    execute_keywords: list[str],
    rollback_keywords: list[str],
) -> str:
    name = file_path.name.lower()
    if any(keyword in name for keyword in rollback_keywords):
        return "rollback"
    if any(keyword in name for keyword in ddl_keywords):
        return "ddl"
    if any(keyword in name for keyword in backup_keywords):
        return "backup"
    if any(keyword in name for keyword in execute_keywords):
        return "execute"
    return "execute"


def _split_files_by_phase(
    files: list[Path],
    *,
    ddl_keywords: list[str],
    backup_keywords: list[str],
    execute_keywords: list[str],
    rollback_keywords: list[str],
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    ddl_files: list[Path] = []
    backup_files: list[Path] = []
    execute_files: list[Path] = []
    rollback_files: list[Path] = []
    for file_path in files:
        phase = _phase_for_sql_file(
            file_path,
            ddl_keywords=ddl_keywords,
            backup_keywords=backup_keywords,
            execute_keywords=execute_keywords,
            rollback_keywords=rollback_keywords,
        )
        if phase == "ddl":
            ddl_files.append(file_path)
        elif phase == "backup":
            backup_files.append(file_path)
        elif phase == "rollback":
            rollback_files.append(file_path)
        else:
            execute_files.append(file_path)
    key_fn = lambda p: p.name.lower()
    return (
        sorted(ddl_files, key=key_fn),
        sorted(backup_files, key=key_fn),
        sorted(execute_files, key=key_fn),
        sorted(rollback_files, key=key_fn),
    )


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


def _format_sql_cell_value(val: object) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, (bytes, bytearray)):
        s = val.decode("utf-8", errors="replace")
    else:
        s = str(val)
    if len(s) > _MAX_QUERY_CELL_LEN:
        return f"{s[:_MAX_QUERY_CELL_LEN]}...(截断,原长{len(s)})"
    return s


def _log_statement_result(
    progress_callback: Callable[[ProgressEvent], None] | None,
    log_lines: list[str],
    cursor: Cursor,
    *,
    phase_name: str,
    file_name: str,
    statement_index: int,
    statement_total: int,
) -> None:
    """
    在 cursor.execute 之后调用：对 SELECT/SHOW 等写出结果集（\\G 风格），
    对 DML/DDL 写影响行数。内容追加到 execution_log，供分文件详情展示。
    """
    desc = cursor.description
    if desc:
        col_names = [d[0] for d in desc]
        try:
            rows = cursor.fetchall() or ()
        except Exception as exc:  # noqa: BLE001
            _emit_progress(
                progress_callback,
                log_lines,
                log=f"\n[结果] 语句 {statement_index}/{statement_total} 读取结果集失败：{exc}\n",
            )
            return
        n = len(rows)
        shown = rows[:_MAX_QUERY_RESULT_ROWS]
        parts: list[str] = [
            f"\n[结果] 语句 {statement_index}/{statement_total} 查询输出（共 {n} 行，"
            f"显示前 {min(n, _MAX_QUERY_RESULT_ROWS)} 行，\\G）\n"
        ]
        for rno, row in enumerate(shown, 1):
            parts.append(f"*************************** {rno}. row ***************************\n")
            if len(col_names) != len(row):
                parts.append(f"  (列数与元组长度不一致: {len(col_names)} vs {len(row)})\n")
            for i, cname in enumerate(col_names):
                cell: object = row[i] if i < len(row) else None
                parts.append(f"  {cname}: {_format_sql_cell_value(cell)}\n")
        if n > _MAX_QUERY_RESULT_ROWS:
            parts.append(
                f"[结果] 已省略 {n - _MAX_QUERY_RESULT_ROWS} 行（单次最多显示 {_MAX_QUERY_RESULT_ROWS} 行）\n"
            )
        text = "".join(parts)
        if len(text) > _MAX_QUERY_RESULT_LOG_CHARS:
            text = (
                text[:_MAX_QUERY_RESULT_LOG_CHARS]
                + f"\n[结果] 本段输出过长已截断（单段上限 {_MAX_QUERY_RESULT_LOG_CHARS} 字符）\n"
            )
        _emit_progress(progress_callback, log_lines, log=text)
        return

    try:
        rc = cursor.rowcount
        if rc is None or int(rc) < 0:
            rc_int = 0
        else:
            rc_int = int(rc)
    except (TypeError, ValueError):
        rc_int = 0
    _emit_progress(
        progress_callback,
        log_lines,
        log=f"\n[结果] 语句 {statement_index}/{statement_total} 无结果集，影响行数: {rc_int}\n",
    )


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

    execution_files = _build_execution_files(folder_abs_path, selected_files)
    if not execution_files:
        return False, "未找到有效 SQL 文件", "未找到有效 SQL 文件"
    ddl_keywords = _parse_keywords(
        getattr(config, "sql_keyword_ddl", ""),
        _DEFAULT_DDL_KEYWORDS,
    )
    backup_keywords = _parse_keywords(
        getattr(config, "sql_keyword_backup", ""),
        _DEFAULT_BACKUP_KEYWORDS,
    )
    execute_keywords = _parse_keywords(
        getattr(config, "sql_keyword_execute", ""),
        _DEFAULT_EXECUTE_KEYWORDS,
    )
    rollback_keywords = _parse_keywords(
        getattr(config, "sql_keyword_rollback", ""),
        _DEFAULT_ROLLBACK_KEYWORDS,
    )
    ddl_files, backup_files, execute_files, rollback_files = _split_files_by_phase(
        execution_files,
        ddl_keywords=ddl_keywords,
        backup_keywords=backup_keywords,
        execute_keywords=execute_keywords,
        rollback_keywords=rollback_keywords,
    )
    if not (ddl_files or backup_files or execute_files or rollback_files):
        return False, "未找到可执行 SQL 文件", "未找到可执行 SQL 文件"
    log_lines: list[str] = []
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
        sequence_counter = 0

        def run_phase(cursor, phase_name: str, files: list[Path]) -> None:
            nonlocal sequence_counter
            if not files:
                _emit_progress(
                    progress_callback,
                    log_lines,
                    log=f"[{phase_name}] 无匹配脚本，跳过\n",
                    tip=f"{phase_name} 跳过",
                )
                return
            for sql_file in files:
                sequence_counter += 1
                sql_content = sql_file.read_text(encoding="utf-8")
                statements = _split_sql_statements(sql_content)
                if not statements:
                    _emit_progress(
                        progress_callback,
                        log_lines,
                        log=f"[{phase_name}] {sql_file.name} 无可执行语句，跳过\n",
                        tip=f"{phase_name}：{sql_file.name}",
                    )
                    continue
                _emit_progress(
                    progress_callback,
                    log_lines,
                    log=f"[{phase_name}] 开始执行 {sql_file.name}，语句数 {len(statements)}\n",
                    tip=f"{phase_name}：{sql_file.name}",
                )
                for idx, statement in enumerate(statements, start=1):
                    cursor.execute(statement)
                    _log_statement_result(
                        progress_callback,
                        log_lines,
                        cursor,
                        phase_name=phase_name,
                        file_name=sql_file.name,
                        statement_index=idx,
                        statement_total=len(statements),
                    )
                    _emit_progress(
                        progress_callback,
                        log_lines,
                        tip=f"{phase_name}：{sql_file.name} 语句 {idx}/{len(statements)}",
                    )
                connection.commit()
                _emit_progress(
                    progress_callback,
                    log_lines,
                    log=f"[{phase_name}] 执行完成 {sql_file.name}\n",
                    tip=f"{phase_name}：{sql_file.name} 已完成",
                )

        with connection.cursor() as cursor:
            try:
                run_phase(cursor, "备份", backup_files)
                run_phase(cursor, "DDL", ddl_files)
                run_phase(cursor, "执行", execute_files)
            except Exception as exc:  # noqa: BLE001
                connection.rollback()
                err_msg = f"[ERROR] 回滚前阶段失败：{exc}"
                _emit_progress(progress_callback, log_lines, log=err_msg + "\n", tip="执行失败，转入回滚")
                if rollback_files:
                    rollback_failed = False
                    rollback_err = ""
                    for rollback_file in rollback_files:
                        try:
                            rollback_content = rollback_file.read_text(encoding="utf-8")
                            rollback_statements = _split_sql_statements(rollback_content)
                            if not rollback_statements:
                                _emit_progress(
                                    progress_callback,
                                    log_lines,
                                    log=f"[回滚] {rollback_file.name} 无可执行语句，跳过\n",
                                    tip=f"回滚中：{rollback_file.name}",
                                )
                                continue
                            _emit_progress(
                                progress_callback,
                                log_lines,
                                log=f"[回滚] 开始执行 {rollback_file.name}，语句数 {len(rollback_statements)}\n",
                                tip=f"回滚中：{rollback_file.name}",
                            )
                            for idx, statement in enumerate(rollback_statements, start=1):
                                cursor.execute(statement)
                                _log_statement_result(
                                    progress_callback,
                                    log_lines,
                                    cursor,
                                    phase_name="回滚",
                                    file_name=rollback_file.name,
                                    statement_index=idx,
                                    statement_total=len(rollback_statements),
                                )
                                _emit_progress(
                                    progress_callback,
                                    log_lines,
                                    tip=f"回滚中：{rollback_file.name} 语句 {idx}/{len(rollback_statements)}",
                                )
                            connection.commit()
                            _emit_progress(
                                progress_callback,
                                log_lines,
                                log=f"[回滚] 执行完成 {rollback_file.name}\n",
                                tip=f"回滚中：{rollback_file.name} 已完成",
                            )
                        except Exception as rollback_exc:  # noqa: BLE001
                            connection.rollback()
                            rollback_failed = True
                            rollback_err = str(rollback_exc)
                            _emit_progress(
                                progress_callback,
                                log_lines,
                                log=f"[ERROR] 回滚失败：{rollback_exc}\n",
                                tip="回滚失败",
                            )
                            break
                    if rollback_failed:
                        return (
                            False,
                            f"SQL 执行失败且回滚失败: {exc}; rollback error: {rollback_err}",
                            "\n".join(log_lines),
                        )
                    return False, f"SQL 执行失败，已执行回滚: {exc}", "\n".join(log_lines)
                _emit_progress(progress_callback, log_lines, log="[WARN] 未找到回滚脚本，无法自动回滚\n", tip="执行失败")
                return False, f"SQL 执行失败: {exc}", "\n".join(log_lines)
        return True, "SQL 执行成功", "\n".join(log_lines)
    finally:
        connection.close()
