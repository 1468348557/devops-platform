from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from branch_create.cron_utils import cron_matches
from branch_create.models import ExportSchedule, HoboRequirementLedger, ReleaseBatch, ReleaseItem
from branch_create.hobo_ledger_views import _hobo_ledger_xlsx_bytes
from branch_create.release_entry_views import _release_entry_xlsx_bytes




class Command(BaseCommand):
    help = "Run due export schedules"

    def add_arguments(self, parser):
        parser.add_argument("--due", action="store_true", help="Only run due schedules")
        parser.add_argument("--schedule-id", type=int, default=None, help="Run a specific schedule by id")

    def handle(self, *args, **options):
        now = timezone.localtime()
        due_only = options["due"]
        schedule_id = options.get("schedule_id")

        if schedule_id:
            schedule = ExportSchedule.objects.filter(pk=schedule_id).first()
            if not schedule:
                self.stderr.write(self.style.ERROR(f"Schedule {schedule_id} not found"))
                return
            self._run_schedule(schedule, now)
            self.stdout.write(self.style.NOTICE("done. executed=1"))
            return

        schedules = ExportSchedule.objects.filter(enabled=True).order_by("id")
        executed = 0
        for schedule in schedules:
            if due_only and not cron_matches(schedule.cron_expr.strip(), now):
                continue
            self._run_schedule(schedule, now)
            executed += 1

        self.stdout.write(self.style.NOTICE(f"done. executed={executed}"))

    def _run_schedule(self, schedule: ExportSchedule, now) -> None:
        try:
            if schedule.export_type == ExportSchedule.ExportType.HOBO_LEDGER:
                self._export_hobo_ledger(schedule)
            elif schedule.export_type == ExportSchedule.ExportType.RELEASE_ENTRY:
                self._export_release_entry(schedule)
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f"[{schedule.name}] export failed: {exc}")
            )
            raise

        schedule.last_run_at = now
        schedule.save(update_fields=["last_run_at", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(f"[{schedule.name}] exported ({schedule.get_export_type_display()})")
        )

    def _export_hobo_ledger(self, schedule: ExportSchedule) -> None:
        output_dir = os.environ.get(
            "HOBO_EXPORT_DIR",
            str(settings.BASE_DIR / ".runtime" / "exports" / "hobo-需求登记台账"),
        )
        os.makedirs(output_dir, exist_ok=True)

        items = list(
            HoboRequirementLedger.objects.active().select_related(
                "project", "created_by", "branch_created_by"
            ).order_by("-applied_date", "-id")
        )

        payload = _hobo_ledger_xlsx_bytes(items)
        timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        filename = f"HOBO需求登记台账-{timestamp}.xlsx"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "wb") as f:
            f.write(payload)

        self.stdout.write(f"  -> {filepath} ({len(items)} 条)")

    def _export_release_entry(self, schedule: ExportSchedule) -> None:
        output_dir = os.environ.get(
            "RELEASE_ENTRY_EXPORT_DIR",
            str(settings.BASE_DIR / ".runtime" / "exports" / "投产征集"),
        )
        os.makedirs(output_dir, exist_ok=True)

        today = timezone.localdate()
        batches = ReleaseBatch.objects.filter(release_date__gte=today).order_by("release_date")

        exported = 0
        for batch in batches:
            items = list(
                ReleaseItem.objects.active().select_related(
                    "project", "developer", "batch"
                ).filter(batch=batch).order_by("-updated_at", "-id")
            )
            if not items:
                continue

            payload = _release_entry_xlsx_bytes(batch, items)
            batch_date = batch.release_date.isoformat()
            filename = f"{batch_date}-投产征集.xlsx"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, "wb") as f:
                f.write(payload)

            exported += 1
            self.stdout.write(f"  -> {filepath} ({len(items)} 条)")

        if exported == 0:
            self.stdout.write("  (无今天及之后的批次数据)")
