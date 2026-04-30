import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from branch_create.views import _run_execute_job


class Command(BaseCommand):
    help = "Run branch execute workflow in detached process."

    def add_arguments(self, parser):
        parser.add_argument("run_id", type=str)
        parser.add_argument("payload_file", type=str)

    def handle(self, *args, **options):
        run_id = (options.get("run_id") or "").strip()
        payload_file = Path(options["payload_file"])
        if not run_id:
            raise CommandError("run_id 不能为空")
        if not payload_file.exists():
            raise CommandError("payload_file 不存在")
        payload = json.loads(payload_file.read_text(encoding="utf-8"))
        payload_file.unlink(missing_ok=True)
        task_refs = payload.get("task_refs") or []
        operator_id = int(payload.get("operator_id") or 0)
        if operator_id <= 0:
            raise CommandError("operator_id 非法")
        _run_execute_job(run_id, task_refs, operator_id)
