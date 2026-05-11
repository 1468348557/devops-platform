from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from branch_create.cron_utils import cron_matches
from branch_create.models import BranchCreateSchedule
from branch_create.services.branch_tasks import run_schedule




class Command(BaseCommand):
    help = "Run due branch creation schedules"

    def add_arguments(self, parser):
        parser.add_argument("--due", action="store_true", help="Only run due schedules")

    def handle(self, *args, **options):
        now = timezone.localtime()
        due_only = options["due"]

        schedules = BranchCreateSchedule.objects.filter(enabled=True).order_by("id")
        executed = 0
        for schedule in schedules:
            if due_only and not cron_matches(schedule.cron_expr.strip(), now):
                continue
            run = run_schedule(schedule, operator=schedule.created_by, trigger_mode="cron")
            executed += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{schedule.name}] {run.status} {run.summary}"
                )
            )

        self.stdout.write(self.style.NOTICE(f"done. executed={executed}"))
