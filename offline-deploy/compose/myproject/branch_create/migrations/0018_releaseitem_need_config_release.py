from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("branch_create", "0017_alter_hobo_requirement_branch"),
    ]

    operations = [
        migrations.AddField(
            model_name="releaseitem",
            name="need_config_release",
            field=models.BooleanField(blank=True, null=True, verbose_name="是否涉及配置文件投产"),
        ),
    ]
