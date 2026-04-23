from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from branch_create.models import BranchCreateSchedule
from branch_create.services.branch_tasks import run_schedule


def _cron_matches(expr: str, now: datetime) -> bool:
    parts = expr.split()
    if len(parts) != 5:
        return False

    minute, hour, dom, month, dow = parts

    def match(token: str, value: int) -> bool:
        if token == "*":
            return True
        if token.startswith("*/"):
            try:
                step = int(token[2:])
                return step > 0 and value % step == 0
            except ValueError:
                return False
        if "," in token:
            return any(match(t.strip(), value) for t in token.split(","))
        try:
            return int(token) == value
        except ValueError:
            return False

    return (
        match(minute, now.minute)
        and match(hour, now.hour)
        and match(dom, now.day)
        and match(month, now.month)
        and match(dow, now.weekday())
    )


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
            if due_only and not _cron_matches(schedule.cron_expr.strip(), now):
                continue
            run = run_schedule(schedule, operator=schedule.created_by, trigger_mode="cron")
            executed += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{schedule.name}] {run.status} {run.summary}"
                )
            )

        self.stdout.write(self.style.NOTICE(f"done. executed={executed}"))
