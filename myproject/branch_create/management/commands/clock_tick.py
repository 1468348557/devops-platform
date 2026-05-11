from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from branch_create.cron_utils import cron_matches
from branch_create.models import BranchCreateSchedule, ExportSchedule
from branch_create.services.branch_tasks import run_schedule


class Command(BaseCommand):
    help = "Clock tick — run all due schedules (branch + export)"

    def handle(self, *args, **options):
        now = timezone.localtime()
        total = 0

        # 分支创建调度
        for schedule in BranchCreateSchedule.objects.filter(enabled=True).order_by("id"):
            if not cron_matches(schedule.cron_expr.strip(), now):
                continue
            run = run_schedule(schedule, operator=schedule.created_by, trigger_mode="cron")
            total += 1
            self.stdout.write(
                self.style.SUCCESS(f"[branch] {schedule.name} {run.status} {run.summary}")
            )

        # 定时导出调度
        for schedule in ExportSchedule.objects.filter(enabled=True).order_by("id"):
            if not cron_matches(schedule.cron_expr.strip(), now):
                continue
            self._run_export(schedule, now)
            total += 1

        self.stdout.write(self.style.NOTICE(f"clock tick done. executed={total}"))

    def _run_export(self, schedule, now) -> None:
        from branch_create.management.commands.run_export_schedules import Command as ExportCmd
        ExportCmd()._run_schedule(schedule, now)
