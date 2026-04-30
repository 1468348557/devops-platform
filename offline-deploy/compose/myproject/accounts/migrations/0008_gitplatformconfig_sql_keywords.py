from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_rolepermissionpolicy_release_entry_editable_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_keyword_backup",
            field=models.CharField(blank=True, default="backup,bak,备份", max_length=255),
        ),
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_keyword_ddl",
            field=models.CharField(blank=True, default="ddl", max_length=255),
        ),
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_keyword_rollback",
            field=models.CharField(blank=True, default="rollback,回滚", max_length=255),
        ),
    ]
