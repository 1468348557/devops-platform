import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("branch_create", "0005_releaseitem_deploy_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="HoboRequirementLedger",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("requirement_type", models.CharField(max_length=64, verbose_name="需求类型")),
                ("description", models.TextField(verbose_name="需求描述")),
                (
                    "applicant_name",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=128,
                        verbose_name="申请人",
                    ),
                ),
                ("applied_date", models.DateField(verbose_name="申请日期")),
                (
                    "base_branch",
                    models.CharField(
                        default="master",
                        max_length=128,
                        verbose_name="依赖分支",
                    ),
                ),
                (
                    "base_branch_contact",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=128,
                        verbose_name="依赖分支联系人",
                    ),
                ),
                (
                    "flowchart_name",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=256,
                        verbose_name="流程图名称",
                    ),
                ),
                (
                    "uat_submit_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        verbose_name="提交 UAT 日期",
                    ),
                ),
                (
                    "rel_submit_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        verbose_name="提交 REL 日期",
                    ),
                ),
                (
                    "production_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        verbose_name="投产日期",
                    ),
                ),
                ("remark", models.TextField(blank=True, default="", verbose_name="备注")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hobo_requirement_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hobo_requirement_entries",
                        to="branch_create.projectcatalog",
                        verbose_name="工程",
                    ),
                ),
            ],
            options={
                "verbose_name": "HOBO 需求登记",
                "verbose_name_plural": "HOBO 需求登记",
                "db_table": "hobo_requirement_ledger",
                "ordering": ["-applied_date", "-id"],
            },
        ),
    ]
