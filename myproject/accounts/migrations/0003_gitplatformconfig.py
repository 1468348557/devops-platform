from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_userprofile_approval_status_userprofile_approved_at_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GitPlatformConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton_key", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("git_base_url", models.CharField(default="https://gitlab.spdb.com", max_length=255)),
                ("git_group", models.CharField(default="zh-1087", max_length=128)),
                ("work_base_dir", models.CharField(default="/workspace/repos", max_length=255)),
                ("git_username", models.CharField(blank=True, default="", max_length=128)),
                ("git_password", models.CharField(blank=True, default="", max_length=255)),
                ("git_pat", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_git_platform_configs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "git_platform_config",
                "verbose_name": "Git 平台配置",
                "verbose_name_plural": "Git 平台配置",
            },
        ),
    ]
