from django.db import migrations, models


def _enable_export_for_system_roles(apps, schema_editor):
    RoleDefinition = apps.get_model("accounts", "RoleDefinition")
    RolePermissionPolicy = apps.get_model("accounts", "RolePermissionPolicy")
    for role in RoleDefinition.objects.filter(key__in=["ops", "developer"]):
        policy = RolePermissionPolicy.objects.filter(role_id=role.id).first()
        if policy:
            policy.action_release_entry_export = True
            policy.save(update_fields=["action_release_entry_export"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_add_menu_notification"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolepermissionpolicy",
            name="action_release_entry_export",
            field=models.BooleanField(default=False, verbose_name="导出投产征集 Excel"),
        ),
        migrations.RunPython(_enable_export_for_system_roles, migrations.RunPython.noop),
    ]
