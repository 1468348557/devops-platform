from django.db import migrations, models


def set_ops_auto_approve_true(apps, schema_editor):
    RolePermissionPolicy = apps.get_model("accounts", "RolePermissionPolicy")
    RoleDefinition = apps.get_model("accounts", "RoleDefinition")
    ops_role = RoleDefinition.objects.filter(key="ops").first()
    if not ops_role:
        return
    RolePermissionPolicy.objects.filter(role=ops_role).update(
        action_sql_request_auto_approve=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_gitplatformconfig_sql_keyword_execute"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolepermissionpolicy",
            name="action_sql_request_auto_approve",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            set_ops_auto_approve_true, migrations.RunPython.noop
        ),
    ]
