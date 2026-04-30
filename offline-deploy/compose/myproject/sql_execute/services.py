from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pymysql
from pymysql.cursors import Cursor

from accounts.models import GitPlatformConfig

ProgressEvent = dict[str, str | int | None]
# 单次结果写入 execution_log 的上限，避免把超大结果集打爆数据库
_MAX_QUERY_RESULT_ROWS = 200
_MAX_QUERY_CELL_LEN = 4000
_MAX_QUERY_RESULT_LOG_CHARS = 60_000

_LOG_STEP = "SQL"


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


def _build_execution_sequence(
    folder_abs_path: Path, selected_files: list[str]
) -> tuple[list[Path], str]:
    """严格按申请单顺序解析路径；任一条无效则整单失败（不丢弃、不重排）。"""
    selected_abs: list[Path] = []
    for idx, raw_path in enumerate(selected_files, start=1):
        stripped = str(raw_path or "").strip()
        if not stripped:
            return [], f"第 {idx} 条路径为空"
        src = Path(stripped)
        candidate = src.resolve() if src.is_absolute() else (folder_abs_path / src).resolve()
        try:
            candidate.relative_to(folder_abs_path)
        except ValueError:
            return [], f"第 {idx} 条不在允许的 SQL 目录内: {stripped}"
        if not candidate.exists() or not candidate.is_file() or candidate.suffix.lower() != ".sql":
            return [], f"第 {idx} 条不是有效 SQL 文件: {stripped}"
        selected_abs.append(candidate)
    return selected_abs, ""


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


def _parse_keywords(raw: str, default_raw: str) -> list[str]:
    source = (raw or "").strip() or default_raw
    parts = [part.strip().lower() for part in source.replace("，", ",").split(",")]
    return [part for part in parts if part]


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

    execution_sequence, seq_err = _build_execution_sequence(folder_abs_path, selected_files)
    if seq_err:
        return False, seq_err, seq_err
    if not execution_sequence:
        return False, "未找到有效 SQL 文件", "未找到有效 SQL 文件"

    # 1. 识别每个 SQL 属于哪个执行类型
    ddl_keywords = _parse_keywords(getattr(config, "sql_keyword_ddl", ""), "ddl")
    backup_keywords = _parse_keywords(getattr(config, "sql_keyword_backup", ""), "backup,bak,备份")
    execute_keywords = _parse_keywords(getattr(config, "sql_keyword_execute", ""), "execute,执行")
    rollback_keywords = _parse_keywords(getattr(config, "sql_keyword_rollback", ""), "rollback,回滚")

    def get_phase(name: str) -> str:
        lowered = name.lower()
        is_ddl = any(k in lowered for k in ddl_keywords)
        is_backup = any(k in lowered for k in backup_keywords)
        is_exec = any(k in lowered for k in execute_keywords)
        is_roll = any(k in lowered for k in rollback_keywords)
        
        hits = []
        if is_ddl: hits.append("ddl")
        if is_backup: hits.append("backup")
        if is_exec: hits.append("execute")
        if is_roll: hits.append("rollback")
        
        if len(hits) == 1:
            return hits[0]
        return ""

    matched_phases = []
    for p in execution_sequence:
        ph = get_phase(p.name)
        if not ph:
            return False, f"文件 {p.name} 无法准确识别唯一脚本类型", f"文件 {p.name} 无法准确识别唯一脚本类型"
        matched_phases.append(ph)

    # 2. 校验是否符合执行顺序
    def parse_rules(raw: str) -> list[list[str]]:
        default_rule = ["backup", "ddl", "execute", "rollback"]
        raw_text = (raw or "").replace("，", ",")
        candidates = [item.strip() for item in raw_text.replace("\n", ";").split(";") if item.strip()]
        if not candidates:
            return [default_rule]
        
        mapping = {"backup": "backup", "备份": "backup", "ddl": "ddl", "execute": "execute", "执行": "execute", "rollback": "rollback", "回滚": "rollback"}
        rules = []
        for cand in candidates:
            norm = [mapping.get(p.strip().lower(), "") for p in cand.split(",")]
            norm = [n for n in norm if n]
            if norm:
                rules.append(norm)
        return rules if rules else [default_rule]
    
    allowed_rules = parse_rules(getattr(config, "sql_auto_approve_order", ""))
    if matched_phases not in allowed_rules:
        display_rules = " | ".join("->".join(r) for r in allowed_rules)
        err = f"执行顺序校验失败（当前顺序: {' -> '.join(matched_phases)}；允许规则: {display_rules}）"
        return False, err, err

    phase_labels = {"backup": "备份", "ddl": "DDL", "execute": "执行", "rollback": "回滚"}

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

    def run_one_sql_file(cursor: Cursor, sql_file: Path, step_label: str) -> None:
        sql_content = sql_file.read_text(encoding="utf-8")
        statements = _split_sql_statements(sql_content)
        if not statements:
            _emit_progress(
                progress_callback,
                log_lines,
                log=f"[{step_label}] {sql_file.name} 无可执行语句，跳过\n",
                tip=f"{step_label}：{sql_file.name}",
            )
            return
        _emit_progress(
            progress_callback,
            log_lines,
            log=f"[{step_label}] 开始执行 {sql_file.name}，语句数 {len(statements)}\n",
            tip=f"{step_label}：{sql_file.name}",
        )
        for idx, statement in enumerate(statements, start=1):
            cursor.execute(statement)
            _log_statement_result(
                progress_callback,
                log_lines,
                cursor,
                phase_name=step_label,
                file_name=sql_file.name,
                statement_index=idx,
                statement_total=len(statements),
            )
            _emit_progress(
                progress_callback,
                log_lines,
                tip=f"{step_label}：{sql_file.name} 语句 {idx}/{len(statements)}",
            )
        connection.commit()
        _emit_progress(
            progress_callback,
            log_lines,
            log=f"[{step_label}] 执行完成 {sql_file.name}\n",
            tip=f"{step_label}：{sql_file.name} 已完成",
        )

    try:
        with connection.cursor() as cursor:
            try:
                for i, sql_file in enumerate(execution_sequence):
                    step_label = phase_labels.get(matched_phases[i], "执行")
                    run_one_sql_file(cursor, sql_file, step_label)
            except Exception as exc:  # noqa: BLE001
                connection.rollback()
                err_msg = f"[ERROR] 执行失败：{exc}"
                _emit_progress(
                    progress_callback,
                    log_lines,
                    log=err_msg + "\n",
                    tip="执行失败",
                )
                return False, f"SQL 执行失败: {exc}", "\n".join(log_lines)
        return True, "SQL 执行成功", "\n".join(log_lines)
    finally:
        connection.close()
