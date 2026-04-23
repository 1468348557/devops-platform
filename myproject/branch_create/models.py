from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils import timezone

BRANCH_REGEX = r"^(FIX|REQ|PUB)-[0-9]{8}-[0-9]{4}$"


class ProjectCatalog(models.Model):
    project_code = models.CharField(max_length=64, unique=True, verbose_name="工程编码")
    project_name = models.CharField(
        max_length=128, blank=True, default="", verbose_name="工程名称"
    )
    enabled = models.BooleanField(default=True, verbose_name="是否启用")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_catalog"
        ordering = ["project_name", "id"]

    def __str__(self) -> str:
        if self.project_name:
            return f"{self.project_name} ({self.project_code})"
        return self.project_code


class ReleaseBatch(models.Model):
    class ReleaseType(models.TextChoices):
        RELEASE = "release", "release"
        HOTFIX = "hotfix", "hotfix"

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        OPEN = "open", "开放填写"
        CLOSED = "closed", "关闭填写"
        EXECUTED = "executed", "已执行"

    release_date = models.DateField(unique=True, verbose_name="投产日期")
    release_type = models.CharField(
        max_length=16,
        choices=ReleaseType.choices,
        default=ReleaseType.RELEASE,
        verbose_name="投产分支类型",
    )
    release_branch = models.CharField(max_length=64, verbose_name="投产分支")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_release_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "release_batch"
        ordering = ["-release_date", "-id"]

    def __str__(self) -> str:
        return f"{self.release_date} / {self.release_branch}"


class ReleaseBatchProject(models.Model):
    batch = models.ForeignKey(ReleaseBatch, on_delete=models.CASCADE, related_name="projects")
    project_code = models.CharField(max_length=64)
    project_name = models.CharField(max_length=128)
    enabled = models.BooleanField(default=True)

    class Meta:
        db_table = "release_batch_project"
        unique_together = ("batch", "project_code")
        ordering = ["project_name", "id"]

    def __str__(self) -> str:
        return f"{self.project_name} ({self.project_code})"


class ReleaseBranchSequence(models.Model):
    class BranchType(models.TextChoices):
        FIX = "FIX", "FIX"
        REQ = "REQ", "REQ"
        PUB = "PUB", "PUB"

    branch_type = models.CharField(max_length=3, choices=BranchType.choices)
    date_str = models.CharField(max_length=8)
    current_serial = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "release_branch_sequence"
        unique_together = ("branch_type", "date_str")


class ReleaseItem(models.Model):
    class LineStatus(models.TextChoices):
        DRAFT = "draft", "草稿"
        INCOMPLETE = "incomplete", "未填写完整"
        SUBMITTED = "submitted", "已提交"
        CONFIRMED = "confirmed", "已确认"
        REJECTED = "rejected", "已驳回"

    branch_type = models.CharField(
        max_length=3,
        choices=ReleaseBranchSequence.BranchType.choices,
        default=ReleaseBranchSequence.BranchType.REQ,
    )
    requirement_branch = models.CharField(
        max_length=32,
        unique=True,
        validators=[RegexValidator(regex=BRANCH_REGEX, message="需求分支格式不正确")],
    )
    batch = models.ForeignKey(ReleaseBatch, on_delete=models.CASCADE, related_name="items")
    project = models.ForeignKey(
        ReleaseBatchProject, on_delete=models.PROTECT, related_name="release_items"
    )
    flow_name = models.CharField(max_length=128)
    biz_category = models.CharField(max_length=64, blank=True, default="")
    release_branch = models.CharField(max_length=64)
    tech_owner = models.CharField(max_length=64)
    biz_owner = models.CharField(max_length=64)

    need_param_release = models.BooleanField(null=True, blank=True)
    param_confirmed = models.BooleanField(null=True, blank=True)
    need_menu = models.BooleanField(null=True, blank=True)
    menu_added = models.BooleanField(null=True, blank=True)
    need_difs = models.BooleanField(null=True, blank=True)
    need_flowchart = models.BooleanField(null=True, blank=True)
    flowchart_checked = models.BooleanField(null=True, blank=True)
    flow_definition_name = models.CharField(max_length=128, blank=True, default="")
    implementation_unit_no = models.CharField(max_length=64, blank=True, default="")
    remark = models.CharField(max_length=255, blank=True, default="")
    need_event_platform = models.BooleanField(null=True, blank=True)
    need_task_pool = models.BooleanField(null=True, blank=True)
    need_bpmp = models.BooleanField(null=True, blank=True)
    need_image = models.BooleanField(null=True, blank=True)
    need_esf = models.BooleanField(null=True, blank=True)
    need_trade_tuning = models.BooleanField(null=True, blank=True)
    need_release_verify = models.BooleanField(null=True, blank=True)
    common_component_branch = models.CharField(max_length=64, blank=True, default="")

    rel_deployed = models.BooleanField(null=True, blank=True)
    deploy_status = models.CharField(max_length=32, blank=True, default="")
    rel_test_status = models.CharField(max_length=32, blank=True, default="")
    branch_created = models.BooleanField(default=False)
    branch_created_at = models.DateTimeField(null=True, blank=True)
    branch_created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_release_item_branches",
    )
    branch_create_error = models.CharField(max_length=255, blank=True, default="")
    branch_create_log = models.TextField(blank=True, default="")

    developer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="release_items",
    )
    line_status = models.CharField(
        max_length=16, choices=LineStatus.choices, default=LineStatus.DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "release_item"
        ordering = ["-updated_at", "-id"]

    @staticmethod
    def _next_requirement_branch(branch_type: str) -> str:
        date_str = timezone.localdate().strftime("%Y%m%d")
        with transaction.atomic():
            seq, _ = ReleaseBranchSequence.objects.select_for_update().get_or_create(
                branch_type=branch_type, date_str=date_str, defaults={"current_serial": 0}
            )
            seq.current_serial += 1
            seq.save(update_fields=["current_serial", "updated_at"])
        return f"{branch_type}-{date_str}-{seq.current_serial:04d}"

    def get_missing_fields(self) -> list[str]:
        missing: list[str] = []
        required_values = {
            "flow_name": self.flow_name,
            "biz_category": self.biz_category,
            "project": self.project_id,
            "requirement_branch": self.requirement_branch,
            "tech_owner": self.tech_owner,
            "biz_owner": self.biz_owner,
            "need_param_release": self.need_param_release,
            "need_menu": self.need_menu,
            "need_difs": self.need_difs,
            "need_flowchart": self.need_flowchart,
            "need_event_platform": self.need_event_platform,
            "need_task_pool": self.need_task_pool,
            "need_bpmp": self.need_bpmp,
            "need_image": self.need_image,
            "need_esf": self.need_esf,
            "need_trade_tuning": self.need_trade_tuning,
            "need_release_verify": self.need_release_verify,
            "rel_test_status": self.rel_test_status,
        }
        for name, value in required_values.items():
            if value in ("", None):
                missing.append(name)

        if self.need_param_release is True and self.param_confirmed is None:
            missing.append("param_confirmed")
        if self.need_menu is True and self.menu_added is None:
            missing.append("menu_added")
        if self.need_flowchart is True and self.flowchart_checked is None:
            missing.append("flowchart_checked")
        if self.need_flowchart is True and not self.flow_definition_name:
            missing.append("flow_definition_name")
        return missing

    def refresh_line_status(self) -> None:
        missing = self.get_missing_fields()
        self.line_status = self.LineStatus.INCOMPLETE if missing else self.LineStatus.DRAFT

    def save(self, *args, **kwargs):
        self.refresh_line_status()
        super().save(*args, **kwargs)


class HoboRequirementLedger(models.Model):
    """HOBO 需求登记台账（研发填写，与投产征集类似的列表 + 弹窗编辑）。"""

    class BranchPrefix(models.TextChoices):
        FIX = "FIX", "FIX"
        REQ = "REQ", "REQ"
        PUB = "PUB", "PUB"

    requirement_type = models.CharField(
        max_length=3,
        choices=BranchPrefix.choices,
        verbose_name="需求类型",
    )
    requirement_branch = models.CharField(
        max_length=32,
        unique=True,
        validators=[RegexValidator(regex=BRANCH_REGEX, message="分支名称格式不正确")],
        verbose_name="分支名称",
    )
    project = models.ForeignKey(
        ProjectCatalog,
        on_delete=models.PROTECT,
        related_name="hobo_requirement_entries",
        verbose_name="工程",
    )
    description = models.TextField(verbose_name="需求描述")
    applicant_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        verbose_name="申请人",
    )
    applied_date = models.DateField(verbose_name="申请日期")
    base_branch = models.CharField(
        max_length=128,
        default="master",
        verbose_name="依赖分支",
    )
    base_branch_contact = models.CharField(
        max_length=128,
        blank=True,
        default="",
        verbose_name="依赖分支联系人",
    )
    flowchart_name = models.CharField(
        max_length=256,
        blank=True,
        default="",
        verbose_name="流程图名称",
    )
    uat_submit_date = models.DateField(null=True, blank=True, verbose_name="提交 UAT 日期")
    rel_submit_date = models.DateField(null=True, blank=True, verbose_name="提交 REL 日期")
    production_date = models.DateField(null=True, blank=True, verbose_name="投产日期")
    remark = models.TextField(blank=True, default="", verbose_name="备注")
    branch_created = models.BooleanField(default=False, verbose_name="是否已建分支")
    branch_created_at = models.DateTimeField(null=True, blank=True, verbose_name="建分支时间")
    branch_created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_hobo_branches",
    )
    branch_create_error = models.CharField(max_length=255, blank=True, default="")
    branch_create_log = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hobo_requirement_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hobo_requirement_ledger"
        ordering = ["-applied_date", "-id"]
        verbose_name = "HOBO 需求登记"
        verbose_name_plural = "HOBO 需求登记"

    def __str__(self) -> str:
        return f"{self.requirement_branch} / {self.project.project_code}"


class BranchCreateSchedule(models.Model):
    class SourceType(models.TextChoices):
        HOBO = "hobo", "HOBO需求登记"
        RELEASE = "release", "投产征集"
        BOTH = "both", "两者"

    name = models.CharField(max_length=64, unique=True)
    enabled = models.BooleanField(default=True)
    cron_expr = models.CharField(max_length=64)
    source_type = models.CharField(max_length=16, choices=SourceType.choices, default=SourceType.BOTH)
    days_back = models.PositiveIntegerField(default=30)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="branch_create_schedules",
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "branch_create_schedule"
        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return self.name


class BranchCreateScheduleRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    class TriggerMode(models.TextChoices):
        MANUAL = "manual", "手动"
        CRON = "cron", "计划任务"

    schedule = models.ForeignKey(
        BranchCreateSchedule, on_delete=models.CASCADE, related_name="runs"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    trigger_mode = models.CharField(
        max_length=16, choices=TriggerMode.choices, default=TriggerMode.MANUAL
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branch_schedule_runs",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    total_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    summary = models.CharField(max_length=255, blank=True, default="")
    log = models.TextField(blank=True, default="")

    class Meta:
        db_table = "branch_create_schedule_run"
        ordering = ["-started_at", "-id"]


class BranchTaskExecuteRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    run_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branch_task_execute_runs",
    )
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
        db_table = "branch_task_execute_run"
        ordering = ["-started_at", "-id"]


class BranchTaskExecuteRunItem(models.Model):
    run = models.ForeignKey(
        BranchTaskExecuteRun, on_delete=models.CASCADE, related_name="items"
    )
    seq = models.PositiveIntegerField(default=0)
    source_type = models.CharField(max_length=16)
    source_id = models.PositiveIntegerField(default=0)
    project_code = models.CharField(max_length=64)
    new_branch = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default="failed")
    message = models.CharField(max_length=255, blank=True, default="")
    log = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "branch_task_execute_run_item"
        ordering = ["seq", "id"]
