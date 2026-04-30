import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("branch_create", "0013_releaseitem_remark"),
    ]

    operations = [
        migrations.AddField(
            model_name="releaseitem",
            name="sql_only_release",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="releaseitem",
            name="requirement_branch",
            field=models.CharField(
                blank=True,
                max_length=32,
                null=True,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="需求分支格式不正确",
                        regex="^(FIX|REQ|PUB)-[0-9]{8}-[0-9]{4}$",
                    )
                ],
            ),
        ),
    ]
