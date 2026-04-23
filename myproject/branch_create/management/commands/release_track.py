from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from branch_create.services.release_track_service import (
    ReleaseTrackError,
    ReleaseTrackOptions,
    ReleaseTrackService,
    build_config_text_from_file,
)


class Command(BaseCommand):
    help = "按批次执行投产追板（Python CLI）"

    def add_arguments(self, parser):
        parser.add_argument("--batch-id", type=int, required=True, help="投产批次 ID")
        parser.add_argument(
            "--config-file",
            default="../功能/投产追版/repos.conf",
            help="配置文件路径（用于 TAG/MERGE/TAG_MESSAGE 及 target_branch 映射）",
        )
        parser.add_argument("--tag-name", default="", help="覆盖配置中的 TAG_NAME")
        parser.add_argument("--merge-message", default="", help="覆盖配置中的 MERGE_MESSAGE")
        parser.add_argument("--tag-message", default="", help="覆盖配置中的 TAG_MESSAGE")
        parser.add_argument(
            "--auto-merge",
            action="store_true",
            default=False,
            help="兼容参数（已禁用）：GitLab 需审批，流程中不自动合并",
        )
        parser.add_argument(
            "--force-tag",
            action="store_true",
            default=False,
            help="兼容参数（已禁用）：远端 tag 已存在时仍会自动跳过",
        )
        parser.add_argument("--yes", action="store_true", default=False, help="跳过交互确认")
        parser.add_argument(
            "--approval-url",
            default="",
            help="管理员审批页 URL，默认 <git_base_url>/zh-1807",
        )
        parser.add_argument(
            "--default-target-branch",
            default="master",
            help="仓库未在配置中声明 target_branch 时使用的默认目标分支",
        )
        parser.add_argument("--work-base-dir", default="", help="仓库本地工作目录，默认读取平台配置")
        parser.add_argument("--dry-run", action="store_true", default=False, help="演练模式，不调用 MR/Tag 写操作")

    def handle(self, *args, **options):
        try:
            config_text = build_config_text_from_file(options["config_file"])
            track_options = ReleaseTrackOptions(
                batch_id=options["batch_id"],
                config_text=config_text,
                tag_name=options["tag_name"],
                merge_message=options["merge_message"],
                tag_message=options["tag_message"],
                auto_merge_mr=False,
                force_tag=options["force_tag"],
                assume_yes=options["yes"],
                approval_url=options["approval_url"],
                default_target_branch=options["default_target_branch"],
                work_base_dir=options["work_base_dir"],
                dry_run=options["dry_run"],
            )
            service = ReleaseTrackService(options=track_options, output=self.stdout.write)
            if options["force_tag"]:
                self.stdout.write("提示：--force-tag 已禁用，远端 tag 已存在时将自动跳过。")
            if options["auto_merge"]:
                self.stdout.write("提示：--auto-merge 已禁用，MR 仅创建不自动合并。")
            summary = service.run()
            if summary.has_failures():
                raise CommandError("投产追板执行完成，但存在失败仓库，请查看上方汇总")
            self.stdout.write(self.style.SUCCESS("投产追板执行完成，全部仓库成功"))
        except ReleaseTrackError as exc:
            raise CommandError(str(exc)) from exc

