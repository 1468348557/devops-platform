from django.conf import settings
from django.db import models


class SqlExecutionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待审批"
        APPROVED = "approved", "审批通过"
        RUNNING = "running", "执行中"
        REJECTED = "rejected", "审批拒绝"
        SUCCESS = "success", "执行成功"
        FAILED = "failed", "执行失败"

    release_date = models.DateField(verbose_name="申请日期")
    folder_path = models.CharField(max_length=255, verbose_name="SQL 目录")
    selected_files_json = models.TextField(blank=True, default="[]", verbose_name="勾选 SQL")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="状态",
    )
    execution_result = models.CharField(max_length=255, blank=True, default="")
    execution_tip = models.CharField(max_length=255, blank=True, default="", verbose_name="执行进度提示")
    execution_log = models.TextField(blank=True, default="")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_sql_requests",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sql_execution_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sql_execution_request"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.folder_path} / {self.get_status_display()}"
