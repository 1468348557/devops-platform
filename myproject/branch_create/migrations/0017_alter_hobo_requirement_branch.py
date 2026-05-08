import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("branch_create", "0016_alter_branchcreateschedule_days_back"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hoborequirementledger",
            name="requirement_branch",
            field=models.CharField(
                max_length=68,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="分支名称格式不正确",
                        regex="^(FIX|REQ|PUB)-[0-9]{8}-[0-9]{4}(-[\\w-]{1,50})?$",
                    )
                ],
                verbose_name="分支名称",
            ),
        ),
    ]
