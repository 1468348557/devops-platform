from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("branch_create", "0015_alter_releaseitem_requirement_branch"),
    ]

    operations = [
        migrations.AlterField(
            model_name="branchcreateschedule",
            name="days_back",
            field=models.IntegerField(default=30, verbose_name="回看天数"),
        ),
    ]
