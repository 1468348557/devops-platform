from django.conf import settings
from django.db import models


class ReleaseTrackRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    run_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    phase = models.CharField(max_length=32, blank=True, default="init")
    approval_status = models.CharField(max_length=16, default="pending")
    approval_url = models.CharField(max_length=255, blank=True, default="")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_release_track_runs",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="release_track_runs",
    )
    batch_id = models.PositiveIntegerField(default=0)
    tag_name = models.CharField(max_length=128, blank=True, default="")
    merge_message = models.CharField(max_length=255, blank=True, default="")
    tag_message = models.CharField(max_length=255, blank=True, default="")
    dry_run = models.BooleanField(default=False)
    total_count = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    tip = models.CharField(max_length=255, blank=True, default="")
    error = models.CharField(max_length=255, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "release_track_run"
        ordering = ["-started_at", "-id"]


class ReleaseTrackRunItem(models.Model):
    run = models.ForeignKey(ReleaseTrackRun, on_delete=models.CASCADE, related_name="items")
    repo = models.CharField(max_length=128)
    release_branch = models.CharField(max_length=128, blank=True, default="")
    target_branch = models.CharField(max_length=128, blank=True, default="")
    stage = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=16, default="pending")
    reason = models.CharField(max_length=255, blank=True, default="")
    pending_count = models.PositiveIntegerField(default=0)
    mr_url = models.CharField(max_length=255, blank=True, default="")
    mr_iid = models.PositiveIntegerField(default=0)
    mr_state = models.CharField(max_length=32, blank=True, default="")
    tag_result = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=32, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "release_track_run_item"
        ordering = ["repo", "id"]
        unique_together = ("run", "repo")
