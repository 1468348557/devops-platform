from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("release_track", "0002_releasetrackrun_approval_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="releasetrackrun",
            name="dry_run",
            field=models.BooleanField(default=False),
        ),
    ]
