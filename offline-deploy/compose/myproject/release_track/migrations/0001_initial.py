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
            name="ReleaseTrackRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_id", models.CharField(max_length=64, unique=True)),
                ("status", models.CharField(choices=[("running", "执行中"), ("success", "成功"), ("failed", "失败")], default="running", max_length=16)),
                ("phase", models.CharField(blank=True, default="init", max_length=32)),
                ("batch_id", models.PositiveIntegerField(default=0)),
                ("tag_name", models.CharField(blank=True, default="", max_length=128)),
                ("merge_message", models.CharField(blank=True, default="", max_length=255)),
                ("tag_message", models.CharField(blank=True, default="", max_length=255)),
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
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="release_track_runs", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "db_table": "release_track_run",
                "ordering": ["-started_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="ReleaseTrackRunItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("repo", models.CharField(max_length=128)),
                ("release_branch", models.CharField(blank=True, default="", max_length=128)),
                ("target_branch", models.CharField(blank=True, default="", max_length=128)),
                ("stage", models.CharField(blank=True, default="", max_length=32)),
                ("status", models.CharField(default="pending", max_length=16)),
                ("reason", models.CharField(blank=True, default="", max_length=255)),
                ("pending_count", models.PositiveIntegerField(default=0)),
                ("mr_url", models.CharField(blank=True, default="", max_length=255)),
                ("mr_iid", models.PositiveIntegerField(default=0)),
                ("mr_state", models.CharField(blank=True, default="", max_length=32)),
                ("tag_result", models.CharField(blank=True, default="", max_length=255)),
                ("source", models.CharField(blank=True, default="", max_length=32)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="release_track.releasetrackrun")),
            ],
            options={
                "db_table": "release_track_run_item",
                "ordering": ["repo", "id"],
                "unique_together": {("run", "repo")},
            },
        ),
    ]

