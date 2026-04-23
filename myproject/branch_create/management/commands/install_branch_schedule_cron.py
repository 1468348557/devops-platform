from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Print crontab entries for branch schedule runner"

    def add_arguments(self, parser):
        parser.add_argument("--every", default="*", help="Minute field, default *")

    def handle(self, *args, **options):
        minute = options["every"]
        manage_path = settings.BASE_DIR / "manage.py"
        python_cmd = "python"
        line = f"{minute} * * * * cd {settings.BASE_DIR} && {python_cmd} {manage_path} run_branch_schedules --due"
        self.stdout.write("建议加入 crontab 的条目：")
        self.stdout.write(line)
