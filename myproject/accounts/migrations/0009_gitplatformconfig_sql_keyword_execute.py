from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_gitplatformconfig_sql_keywords"),
    ]

    operations = [
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_keyword_execute",
            field=models.CharField(blank=True, default="execute,执行", max_length=255),
        ),
    ]
