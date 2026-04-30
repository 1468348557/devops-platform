import django.core.validators
from django.db import migrations, models, transaction


def forwards(apps, schema_editor):
    Hobo = apps.get_model("branch_create", "HoboRequirementLedger")
    Seq = apps.get_model("branch_create", "ReleaseBranchSequence")
    for row in Hobo.objects.all():
        raw = (row.requirement_type or "").strip()
        bt = raw if raw in ("FIX", "REQ", "PUB") else "REQ"
        row.requirement_type = bt
        if row.requirement_branch:
            row.save(update_fields=["requirement_type"])
            continue
        date_str = row.applied_date.strftime("%Y%m%d")
        with transaction.atomic():
            seq, _ = Seq.objects.select_for_update().get_or_create(
                branch_type=bt,
                date_str=date_str,
                defaults={"current_serial": 0},
            )
            seq.current_serial += 1
            seq.save()
        row.requirement_branch = f"{bt}-{date_str}-{seq.current_serial:04d}"
        row.save(update_fields=["requirement_type", "requirement_branch"])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("branch_create", "0006_hobo_requirement_ledger"),
    ]

    operations = [
        migrations.AddField(
            model_name="hoborequirementledger",
            name="requirement_branch",
            field=models.CharField(
                blank=True,
                max_length=32,
                null=True,
                verbose_name="分支名称",
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="hoborequirementledger",
            name="requirement_branch",
            field=models.CharField(
                max_length=32,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        regex=r"^(FIX|REQ|PUB)-[0-9]{8}-[0-9]{4}$",
                        message="分支名称格式不正确",
                    )
                ],
                verbose_name="分支名称",
            ),
        ),
        migrations.AlterField(
            model_name="hoborequirementledger",
            name="requirement_type",
            field=models.CharField(
                choices=[("FIX", "FIX"), ("REQ", "REQ"), ("PUB", "PUB")],
                max_length=3,
                verbose_name="需求类型",
            ),
        ),
    ]
