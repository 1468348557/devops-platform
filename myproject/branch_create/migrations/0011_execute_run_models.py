from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("branch_create", "0010_branch_create_log"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BranchTaskExecuteRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_id", models.CharField(max_length=64, unique=True)),
                ("status", models.CharField(choices=[("running", "执行中"), ("success", "成功"), ("failed", "失败")], default="running", max_length=16)),
                ("total_count", models.PositiveIntegerField(default=0)),
                ("processed_count", models.PositiveIntegerField(default=0)),
                ("success_count", models.PositiveIntegerField(default=0)),
                ("skipped_count", models.PositiveIntegerField(default=0)),
                ("failed_count", models.PositiveIntegerField(default=0)),
                ("tip", models.CharField(blank=True, default="", max_length=255)),
                ("error", models.CharField(blank=True, default="", max_length=255)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="branch_task_execute_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "branch_task_execute_run",
                "ordering": ["-started_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="BranchTaskExecuteRunItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("seq", models.PositiveIntegerField(default=0)),
                ("source_type", models.CharField(max_length=16)),
                ("source_id", models.PositiveIntegerField(default=0)),
                ("project_code", models.CharField(max_length=64)),
                ("new_branch", models.CharField(max_length=64)),
                ("status", models.CharField(default="failed", max_length=16)),
                ("message", models.CharField(blank=True, default="", max_length=255)),
                ("log", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="branch_create.branchtaskexecuterun",
                    ),
                ),
            ],
            options={
                "db_table": "branch_task_execute_run_item",
                "ordering": ["seq", "id"],
            },
        ),
    ]
