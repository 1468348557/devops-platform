import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from accounts.models import RoleDefinition, RolePermissionPolicy, UserProfile
from sql_execute.models import SqlExecutionRequest
from sql_execute.services import _log_statement_result, execute_sql_request
from sql_execute.views import (
    _build_execution_parsed,
    _machine_review_sql_files,
    _nearest_future_release_date_str,
    _parse_sql_execution_log,
)


class SqlStatementResultLogTests(TestCase):
    def test_log_select_rows_appends_g_style(self):
        class _Cur:
            description = (("id", int), ("name", str))
            rowcount = 2

            def __init__(self):
                self._rows = [(1, "a"), (2, "b")]

            def fetchall(self):
                return self._rows

        log_lines: list[str] = []
        _log_statement_result(
            None,
            log_lines,
            _Cur(),  # type: ignore[arg-type]
            phase_name="执行",
            file_name="q.sql",
            statement_index=1,
            statement_total=1,
        )
        text = "\n".join(log_lines)
        self.assertIn("1. row", text)
        self.assertIn("id:", text)
        self.assertIn("name:", text)
        self.assertIn("[结果]", text)

    def test_log_dml_shows_affected(self):
        class _Cur:
            description = None
            rowcount = 3

            def fetchall(self):
                return ()

        log_lines: list[str] = []
        _log_statement_result(
            None,
            log_lines,
            _Cur(),  # type: ignore[arg-type]
            phase_name="执行",
            file_name="u.sql",
            statement_index=1,
            statement_total=1,
        )
        self.assertIn("影响行数: 3", "\n".join(log_lines))


class SqlExecutionLogParserTests(TestCase):
    def test_empty_log(self):
        out = _parse_sql_execution_log("")
        self.assertEqual(out["files"], [])
        self.assertEqual(out.get("orphan_log", ""), "")

    def test_parse_skips_and_execute_file(self):
        log = (
            "任务已提交\n"
            "[备份] 无匹配脚本，跳过\n"
            "[DDL] 无匹配脚本，跳过\n"
            "[执行] 开始执行 执行.sql，语句数 2\n"
            "[执行] 执行完成 执行.sql\n"
        )
        out = _parse_sql_execution_log(log)
        self.assertEqual(len(out["files"]), 3)
        self.assertEqual(out["files"][0]["outcome"], "skip_phase")
        self.assertEqual(out["files"][1]["outcome"], "skip_phase")
        self.assertEqual(out["files"][2]["outcome"], "success")
        self.assertEqual(out["files"][2]["file_name"], "执行.sql")
        self.assertEqual(out["files"][2]["phase"], "执行")
        self.assertIn("任务已提交", out.get("orphan_log", "") or "")

    def test_parse_empty_file_skip(self):
        log = "[执行] n.sql 无可执行语句，跳过\n"
        out = _parse_sql_execution_log(log)
        self.assertEqual(len(out["files"]), 1)
        self.assertEqual(out["files"][0]["outcome"], "skip_empty")
        self.assertEqual(out["files"][0]["file_name"], "n.sql")

    def test_error_line_attached_to_current_file(self):
        log = (
            "[执行] 开始执行 a.sql，语句数 1\n"
            "[ERROR] 回滚前阶段失败：boom\n"
        )
        out = _parse_sql_execution_log(log)
        self.assertTrue(any("[ERROR]" in f.get("log", "") for f in out["files"]))

    def test_build_execution_parsed_adds_g_fields(self):
        class _R:
            execution_log = "[执行] 开始执行 x.sql，语句数 1\n[执行] 执行完成 x.sql\n"

            def get_status_display(self):
                return "执行成功"

        parsed = _build_execution_parsed(_R())  # type: ignore[arg-type]
        self.assertEqual(parsed["summary"]["overall"], "执行成功")
        self.assertTrue(parsed["files"])
        self.assertTrue(any("g_fields" in f for f in parsed["files"]))

    def test_progress_api_includes_execution_parsed(self):
        user = User.objects.create_superuser(
            username="logparser_adm", email="a@a.com", password="p"
        )
        row = SqlExecutionRequest.objects.create(
            release_date=date.today(),
            folder_path="/tmp",
            selected_files_json=json.dumps(["/tmp/x.sql"], ensure_ascii=False),
            status=SqlExecutionRequest.Status.SUCCESS,
            requested_by=user,
            execution_log="[执行] 开始执行 x.sql，语句数 1\n[执行] 执行完成 x.sql\n",
        )
        self.client.force_login(user)
        resp = self.client.get(f"/sql-execute/api/request/progress/?request_id={row.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("execution_parsed", data)
        self.assertEqual(data["execution_parsed"]["summary"]["overall"], "执行成功")
        self.assertTrue(len(data["execution_parsed"]["files"]) >= 1)


class NearestReleaseDateStrTests(TestCase):
    def test_picks_today_when_in_list(self):
        self.assertEqual(
            _nearest_future_release_date_str(
                [date(2026, 4, 20), date(2026, 4, 23), date(2026, 5, 1)],
                date(2026, 4, 23),
            ),
            "2026-04-23",
        )

    def test_picks_next_batch_when_today_not_listed(self):
        self.assertEqual(
            _nearest_future_release_date_str(
                [date(2026, 4, 20), date(2026, 4, 23), date(2026, 5, 1)],
                date(2026, 4, 24),
            ),
            "2026-05-01",
        )

    def test_empty_when_all_before_today(self):
        self.assertEqual(
            _nearest_future_release_date_str(
                [date(2026, 4, 10), date(2026, 4, 15)],
                date(2026, 4, 24),
            ),
            "",
        )


class SqlExecutionOrderTests(TestCase):
    @patch("sql_execute.services.GitPlatformConfig.get_solo_safe")
    @patch("sql_execute.services.pymysql.connect")
    def test_execute_in_submit_order_including_forward_rollback(self, mocked_connect, mocked_get_config):
        config = mocked_get_config.return_value
        config.sql_db_host = "127.0.0.1"
        config.sql_db_name = "demo"
        config.sql_db_user = "demo"
        config.sql_db_password = "demo"
        config.sql_db_port = 3306
        config.sql_repo_path = "/tmp/repo"
        config.sql_keyword_ddl = ""
        config.sql_keyword_backup = ""
        config.sql_keyword_execute = ""
        config.sql_keyword_rollback = ""
        config.sql_auto_approve_order = ""
        connection = mocked_connect.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_execute.sql"
            rollback = folder / "99_rollback.sql"
            ddl.write_text("select 11;", encoding="utf-8")
            backup.write_text("select 22;", encoding="utf-8")
            execute.write_text("select 33;", encoding="utf-8")
            rollback.write_text("select 99;", encoding="utf-8")

            selected = json.dumps([str(backup), str(ddl), str(execute), str(rollback)], ensure_ascii=False)
            ok, err, _ = execute_sql_request(str(folder), selected)

        self.assertTrue(ok, msg=f"Execution failed: {err}")
        executed_sql = [args[0] for args, _kwargs in cursor.execute.call_args_list]
        self.assertEqual(executed_sql, ["select 22", "select 11", "select 33", "select 99"])

    @patch("sql_execute.services.GitPlatformConfig.get_solo_safe")
    @patch("sql_execute.services.pymysql.connect")
    def test_same_file_twice_runs_twice(self, mocked_connect, mocked_get_config):
        config = mocked_get_config.return_value
        config.sql_db_host = "127.0.0.1"
        config.sql_db_name = "demo"
        config.sql_db_user = "demo"
        config.sql_db_password = "demo"
        config.sql_db_port = 3306
        config.sql_repo_path = "/tmp/repo"
        config.sql_keyword_ddl = ""
        config.sql_keyword_backup = ""
        config.sql_keyword_execute = ""
        config.sql_keyword_rollback = ""
        config.sql_auto_approve_order = ""
        connection = mocked_connect.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            twice = folder / "run_twice_execute.sql"
            twice.write_text("select 42;", encoding="utf-8")
            path = str(twice)
            selected = json.dumps([path, path], ensure_ascii=False)
            config.sql_auto_approve_order = "execute,execute"
            ok, err, _ = execute_sql_request(str(folder), selected)

        self.assertTrue(ok, msg=f"Execution failed: {err}")
        executed_sql = [args[0] for args, _kwargs in cursor.execute.call_args_list]
        self.assertEqual(executed_sql, ["select 42", "select 42"])

    @patch("sql_execute.services.GitPlatformConfig.get_solo_safe")
    @patch("sql_execute.services.pymysql.connect")
    def test_fail_stops_without_auto_rollback(self, mocked_connect, mocked_get_config):
        config = mocked_get_config.return_value
        config.sql_db_host = "127.0.0.1"
        config.sql_db_name = "demo"
        config.sql_db_user = "demo"
        config.sql_db_password = "demo"
        config.sql_db_port = 3306
        config.sql_repo_path = "/tmp/repo"
        config.sql_keyword_ddl = ""
        config.sql_keyword_backup = ""
        config.sql_keyword_execute = ""
        config.sql_keyword_rollback = ""
        config.sql_auto_approve_order = ""
        connection = mocked_connect.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        call_state = {"count": 0}

        def execute_side_effect(sql):
            call_state["count"] += 1
            if call_state["count"] == 1:
                raise RuntimeError("boom")
            return None

        cursor.execute.side_effect = execute_side_effect

        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            (folder / "01_ddl.sql").write_text("select 1;", encoding="utf-8")
            (folder / "99_rollback.sql").write_text("select 2;", encoding="utf-8")
            selected = json.dumps(
                [str(folder / "01_ddl.sql"), str(folder / "99_rollback.sql")],
                ensure_ascii=False,
            )
            config.sql_auto_approve_order = "ddl,rollback"
            ok, message, _ = execute_sql_request(str(folder), selected)

        self.assertFalse(ok)
        self.assertNotIn("已执行回滚", message)
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertGreaterEqual(connection.rollback.call_count, 1)


class SqlMachineReviewTests(TestCase):
    def test_accept_when_missing_ddl_and_backup(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            execute.write_text("use demo;\nupdate demo_table set id = 1;", encoding="utf-8")
            rollback.write_text("use demo;\ndrop table if exists demo_table;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [execute, rollback],
                "demo",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertTrue(ok)
        self.assertEqual(message, "")

    def test_accept_when_use_line_has_comment_or_fullwidth_semicolon(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            ddl.write_text("use test； -- db select\ncreate table if not exists t(id int);", encoding="utf-8")
            backup.write_text("use test; # backup db\nselect 1;", encoding="utf-8")
            execute.write_text("use test;\nupdate t set id = 2;", encoding="utf-8")
            rollback.write_text("use test;\ndrop table if exists t;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [ddl, backup, execute, rollback],
                "test",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertTrue(ok)
        self.assertEqual(message, "")

    def test_accept_when_multiline_comment_before_use(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            ddl.write_text(
                "/*\nheader comment\n*/\nuse test;\ncreate table if not exists t(id int);",
                encoding="utf-8",
            )
            backup.write_text("use test;\nselect 1;", encoding="utf-8")
            execute.write_text("use test;\nupdate t set id = 2;", encoding="utf-8")
            rollback.write_text("use test;\ndrop table if exists t;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [ddl, backup, execute, rollback],
                "test",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertTrue(ok)
        self.assertEqual(message, "")

    def test_accept_when_utf8_bom_prefix_exists(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            ddl.write_bytes("\ufeffuse test;\ncreate table if not exists t(id int);".encode("utf-8"))
            backup.write_text("use test;\nselect 1;", encoding="utf-8")
            execute.write_text("use test;\nupdate t set id = 2;", encoding="utf-8")
            rollback.write_text("use test;\ndrop table if exists t;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [ddl, backup, execute, rollback],
                "test",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertTrue(ok)
        self.assertEqual(message, "")

    def test_accept_when_sql_db_name_not_configured(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            ddl.write_text("select 1;", encoding="utf-8")
            backup.write_text("select 2;", encoding="utf-8")
            execute.write_text("select 3;", encoding="utf-8")
            rollback.write_text("select 4;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [ddl, backup, execute, rollback],
                "",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertTrue(ok)
        self.assertEqual(message, "")

    def test_reject_when_missing_required_tag(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            bad = folder / "01_unknown.sql"
            bad.write_text("use demo;\nselect 1;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [bad],
                "demo",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertFalse(ok)
        self.assertIn("无法匹配脚本类型", message)

    def test_reject_when_missing_use_db_first_line(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            ddl.write_text("select 1;\nuse demo;", encoding="utf-8")
            backup.write_text("use demo;\nselect 2;", encoding="utf-8")
            execute.write_text("use demo;\nselect 3;", encoding="utf-8")
            rollback.write_text("use demo;\nselect 4;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [ddl, backup, execute, rollback],
                "demo",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertFalse(ok)
        self.assertIn("首行必须是 use demo;", message)

    def test_accept_when_all_rules_match(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            ddl.write_text(
                "use demo;\ncreate table if not exists demo_table(id bigint primary key);",
                encoding="utf-8",
            )
            backup.write_text("use demo;\ninsert into backup_table values (1);", encoding="utf-8")
            execute.write_text("use demo;\nupdate demo_table set id = 1;", encoding="utf-8")
            rollback.write_text("use demo;\ndrop table if exists demo_table;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [ddl, backup, execute, rollback],
                "demo",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertTrue(ok)
        self.assertEqual(message, "")

    def test_reject_ddl_when_any_create_table_not_if_not_exists(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            ddl.write_text(
                "use demo;\n"
                "create table if not exists a(id int);\n"
                "create table b(id int);\n",
                encoding="utf-8",
            )
            backup.write_text("use demo;\nselect 1;", encoding="utf-8")
            execute.write_text("use demo;\nselect 2;", encoding="utf-8")
            rollback.write_text("use demo;\ndrop table if exists a;", encoding="utf-8")
            ok, message = _machine_review_sql_files(
                [ddl, backup, execute, rollback],
                "demo",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertFalse(ok)
        self.assertIn("create table if not exists", message)

    def test_reject_rollback_when_any_drop_table_not_if_exists(self):
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            ddl = folder / "01_ddl.sql"
            backup = folder / "02_备份.sql"
            execute = folder / "03_执行.sql"
            rollback = folder / "04_回滚.sql"
            ddl.write_text("use demo;\nselect 1;", encoding="utf-8")
            backup.write_text("use demo;\nselect 2;", encoding="utf-8")
            execute.write_text("use demo;\nselect 3;", encoding="utf-8")
            rollback.write_text(
                "use demo;\n"
                "drop table if exists a;\n"
                "drop table b;\n",
                encoding="utf-8",
            )
            ok, message = _machine_review_sql_files(
                [ddl, backup, execute, rollback],
                "demo",
                ["ddl"],
                ["backup", "bak", "备份"],
                ["execute", "执行"],
                ["rollback", "回滚"],
            )
        self.assertFalse(ok)
        self.assertIn("drop table if exists", message)


class SqlApprovalPermissionTests(TestCase):
    def setUp(self):
        self.ops_role = RoleDefinition.get_by_key("ops")
        if not self.ops_role:
            self.ops_role = RoleDefinition.objects.create(
                key="ops",
                name="运维",
                is_system=True,
                enabled=True,
                can_be_registered=True,
                is_staff_role=True,
            )
        self.dev_role = RoleDefinition.get_by_key("developer")
        if not self.dev_role:
            self.dev_role = RoleDefinition.objects.create(
                key="developer",
                name="研发",
                is_system=True,
                enabled=True,
                can_be_registered=True,
                is_staff_role=False,
            )
        RolePermissionPolicy.get_for_role(self.ops_role)
        RolePermissionPolicy.get_for_role(self.dev_role)
        self.superuser = User.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="pass1234",
        )
        self.ops_user = User.objects.create_user(
            username="ops",
            email="ops@example.com",
            password="pass1234",
            is_staff=True,
        )
        UserProfile.objects.create(
            user=self.ops_user,
            role=self.ops_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )
        self.dev_user = User.objects.create_user(
            username="dev",
            email="dev@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=self.dev_user,
            role=self.dev_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )
        self.request_row = SqlExecutionRequest.objects.create(
            release_date=timezone.localdate(),
            folder_path="/tmp/sql",
            selected_files_json=json.dumps(["a.sql"]),
            status=SqlExecutionRequest.Status.PENDING,
            requested_by=self.dev_user,
        )

    def test_ops_user_cannot_approve_sql_request(self):
        self.client.force_login(self.ops_user)
        resp = self.client.post(
            "/sql-execute/api/request/action/",
            {"request_id": self.request_row.id, "action": "approve"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_user_with_sql_edit_others_can_view_other_request_progress(self):
        viewer_role = RoleDefinition.objects.create(
            key="sql_viewer",
            name="SQL观察员",
            enabled=True,
            can_be_registered=False,
            is_staff_role=False,
        )
        viewer_policy = RolePermissionPolicy.get_for_role(viewer_role)
        viewer_policy.menu_sql_execute = True
        viewer_policy.action_sql_request_edit_others = True
        viewer_policy.save(
            update_fields=[
                "menu_sql_execute",
                "action_sql_request_edit_others",
                "updated_at",
            ]
        )
        viewer = User.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="pass1234",
        )
        UserProfile.objects.create(
            user=viewer,
            role=viewer_role,
            approval_status=UserProfile.ApprovalStatus.APPROVED,
        )

        self.client.force_login(viewer)
        resp = self.client.get(
            f"/sql-execute/api/request/progress/?request_id={self.request_row.id}",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])

    @patch("sql_execute.views._spawn_detached_command")
    def test_superuser_can_approve_and_triggers_background_command(self, mocked_spawn):
        self.client.force_login(self.superuser)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(
                "/sql-execute/api/request/action/",
                {"request_id": self.request_row.id, "action": "approve"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "running")
        self.request_row.refresh_from_db()
        self.assertEqual(self.request_row.status, SqlExecutionRequest.Status.RUNNING)
        mocked_spawn.assert_called_once()
