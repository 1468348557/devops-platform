import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from branch_create.services.release_track_service import ReleaseTrackOptions
from release_track.views import _run_release_track_worker


class Command(BaseCommand):
    help = "Run release track workflow in detached process."

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
        release_options = ReleaseTrackOptions(
            batch_id=int(payload.get("batch_id") or 0),
            config_text=str(payload.get("config_text") or ""),
            tag_name=str(payload.get("tag_name") or ""),
            merge_message=str(payload.get("merge_message") or ""),
            tag_message=str(payload.get("tag_message") or ""),
            auto_merge_mr=bool(payload.get("auto_merge_mr", False)),
            force_tag=bool(payload.get("force_tag", False)),
            assume_yes=bool(payload.get("assume_yes", True)),
            approval_url=str(payload.get("approval_url") or ""),
            default_target_branch=str(payload.get("default_target_branch") or "master"),
            work_base_dir=str(payload.get("work_base_dir") or ""),
            dry_run=bool(payload.get("dry_run", False)),
            skip_tag=bool(payload.get("skip_tag", False)),
            selected_projects=list(payload.get("selected_projects") or []),
        )
        if release_options.batch_id <= 0:
            raise CommandError("batch_id 非法")
        _run_release_track_worker(run_id, release_options)
