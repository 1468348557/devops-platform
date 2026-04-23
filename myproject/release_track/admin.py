from django.contrib import admin
from .models import ReleaseTrackRun, ReleaseTrackRunItem


@admin.register(ReleaseTrackRun)
class ReleaseTrackRunAdmin(admin.ModelAdmin):
    list_display = (
        "run_id",
        "status",
        "phase",
        "approval_status",
        "batch_id",
        "success_count",
        "skipped_count",
        "failed_count",
        "triggered_by",
        "started_at",
        "finished_at",
    )
    search_fields = ("run_id", "tag_name", "merge_message", "triggered_by__username")
    list_filter = ("status", "phase", "approval_status")
    readonly_fields = ("started_at", "finished_at", "updated_at")


@admin.register(ReleaseTrackRunItem)
class ReleaseTrackRunItemAdmin(admin.ModelAdmin):
    list_display = ("run", "repo", "stage", "status", "reason", "pending_count", "mr_iid")
    search_fields = ("repo", "reason", "mr_url", "tag_result")
    list_filter = ("stage", "status")
    readonly_fields = ("created_at", "updated_at")
