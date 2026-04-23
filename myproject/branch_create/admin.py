from django.contrib import admin

from .models import (
    BranchCreateSchedule,
    BranchCreateScheduleRun,
    HoboRequirementLedger,
    ProjectCatalog,
    ReleaseBatch,
    ReleaseBatchProject,
    ReleaseBranchSequence,
    ReleaseItem,
)


class ReleaseBatchProjectInline(admin.TabularInline):
    model = ReleaseBatchProject
    extra = 1


@admin.register(ReleaseBatch)
class ReleaseBatchAdmin(admin.ModelAdmin):
    list_display = ("release_date", "release_type", "release_branch", "status", "created_by")
    list_filter = ("status", "release_type", "release_date")
    search_fields = ("release_branch", "created_by__username")
    inlines = [ReleaseBatchProjectInline]


@admin.register(ReleaseItem)
class ReleaseItemAdmin(admin.ModelAdmin):
    list_display = (
        "requirement_branch",
        "branch_created",
        "batch",
        "project",
        "developer",
        "line_status",
        "updated_at",
    )
    list_filter = ("line_status", "branch_type", "batch__release_date")
    search_fields = (
        "requirement_branch",
        "flow_name",
        "project__project_name",
        "developer__username",
    )


@admin.register(ReleaseBatchProject)
class ReleaseBatchProjectAdmin(admin.ModelAdmin):
    list_display = ("batch", "project_code", "project_name", "enabled")
    list_filter = ("enabled", "batch__release_date")
    search_fields = ("project_code", "project_name")


@admin.register(ReleaseBranchSequence)
class ReleaseBranchSequenceAdmin(admin.ModelAdmin):
    list_display = ("branch_type", "date_str", "current_serial", "updated_at")
    list_filter = ("branch_type", "date_str")


@admin.register(HoboRequirementLedger)
class HoboRequirementLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "requirement_branch",
        "branch_created",
        "requirement_type",
        "project",
        "applicant_name",
        "applied_date",
        "base_branch",
        "created_by",
        "updated_at",
    )
    list_filter = ("applied_date", "requirement_type")
    search_fields = (
        "requirement_branch",
        "requirement_type",
        "description",
        "applicant_name",
        "project__project_code",
        "created_by__username",
    )


@admin.register(ProjectCatalog)
class ProjectCatalogAdmin(admin.ModelAdmin):
    list_display = ("project_code", "project_name", "enabled", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("project_code", "project_name")


@admin.register(BranchCreateSchedule)
class BranchCreateScheduleAdmin(admin.ModelAdmin):
    list_display = ("name", "source_type", "cron_expr", "days_back", "enabled", "last_run_at")
    list_filter = ("enabled", "source_type")
    search_fields = ("name", "cron_expr")


@admin.register(BranchCreateScheduleRun)
class BranchCreateScheduleRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "schedule",
        "status",
        "trigger_mode",
        "total_count",
        "success_count",
        "skipped_count",
        "failed_count",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "trigger_mode", "started_at")
    search_fields = ("schedule__name", "summary")
