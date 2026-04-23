from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SqlExecutionRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("release_date", models.DateField(verbose_name="申请日期")),
                ("folder_path", models.CharField(max_length=255, verbose_name="SQL 目录")),
                ("selected_files_json", models.TextField(blank=True, default="[]", verbose_name="勾选 SQL")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "待审批"),
                            ("approved", "审批通过"),
                            ("rejected", "审批拒绝"),
                            ("success", "执行成功"),
                            ("failed", "执行失败"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("execution_result", models.CharField(blank=True, default="", max_length=255)),
                ("execution_log", models.TextField(blank=True, default="")),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("executed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_sql_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sql_execution_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "sql_execution_request",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
