from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("release_track", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="releasetrackrun",
            name="approval_status",
            field=models.CharField(default="pending", max_length=16),
        ),
        migrations.AddField(
            model_name="releasetrackrun",
            name="approval_url",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="releasetrackrun",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="releasetrackrun",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="approved_release_track_runs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]

