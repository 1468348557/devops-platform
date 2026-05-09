import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("branch_create", "0018_releaseitem_need_config_release"),
    ]

    operations = [
        migrations.AlterField(
            model_name="releaseitem",
            name="requirement_branch",
            field=models.CharField(
                blank=True,
                max_length=68,
                null=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="需求分支格式不正确",
                        regex=r"^(FIX|REQ|PUB)-[0-9]{8}-[0-9]{4}(-[\w-]{1,50})?$",
                    )
                ],
            ),
        ),
    ]
