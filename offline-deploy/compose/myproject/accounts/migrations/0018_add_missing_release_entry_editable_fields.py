# Generated

from django.db import migrations


def _append_missing_editable_fields(apps, schema_editor):
    RolePermissionPolicy = apps.get_model("accounts", "RolePermissionPolicy")
    missing_dev_fields = ["need_config_release", "is_bug_fix", "bug_reporter"]
    for policy in RolePermissionPolicy.objects.select_related("role").all():
        role_key = getattr(policy.role, "key", "")
        if role_key == "developer":
            current = list(policy.release_entry_editable_fields or [])
            updated = False
            for field in missing_dev_fields:
                if field not in current:
                    current.append(field)
                    updated = True
            if updated:
                policy.release_entry_editable_fields = current
                policy.save(update_fields=["release_entry_editable_fields"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0017_rolepermissionpolicy_action_sql_request_delete"),
    ]

    operations = [
        migrations.RunPython(_append_missing_editable_fields, migrations.RunPython.noop),
    ]
