from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Print crontab entries for all scheduled tasks (branch + export) via clock_tick"

    def add_arguments(self, parser):
        parser.add_argument("--every", default="*", help="Minute field, default *")

    def handle(self, *args, **options):
        minute = options["every"]
        manage_path = settings.BASE_DIR / "manage.py"
        python_cmd = "python"

        self.stdout.write("建议加入 crontab 的条目（时钟调度，覆盖所有类型）：")
        line = (
            f"{minute} * * * * cd {settings.BASE_DIR}"
            f" && {python_cmd} {manage_path} clock_tick"
        )
        self.stdout.write(line)
        self.stdout.write()
        self.stdout.write(
            "clock_tick 会统一检查 BranchCreateSchedule 和 ExportSchedule 的 cron 表达式，"
            "执行所有到期的调度。"
        )
