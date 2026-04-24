from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_rolepermissionpolicy_sql_auto_approve"),
    ]

    operations = [
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_auto_approve_order",
            field=models.CharField(
                blank=True,
                default="backup,ddl,execute,rollback",
                max_length=255,
            ),
        ),
    ]
