from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_gitplatformconfig"),
    ]

    operations = [
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_db_host",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_db_name",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_db_password",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_db_port",
            field=models.PositiveIntegerField(default=3306),
        ),
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_db_user",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_repo_path",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="gitplatformconfig",
            name="sql_repo_clone_url",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
