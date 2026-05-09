from django.db import migrations, models


def _clear_literal_master_dependency(apps, schema_editor):
    """旧版将「未填依赖分支」保存为字面量 master；现改为空串，仅占位登记。"""
    HoboRequirementLedger = apps.get_model("branch_create", "HoboRequirementLedger")
    HoboRequirementLedger.objects.filter(base_branch="master").update(base_branch="")


class Migration(migrations.Migration):

    dependencies = [
        ("branch_create", "0019_alter_releaseitem_requirement_branch"),
    ]

    operations = [
        migrations.RunPython(_clear_literal_master_dependency, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="hoborequirementledger",
            name="base_branch",
            field=models.CharField(blank=True, default="", max_length=128, verbose_name="依赖分支"),
        ),
    ]
