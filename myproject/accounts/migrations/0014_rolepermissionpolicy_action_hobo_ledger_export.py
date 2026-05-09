from django.db import migrations, models


def _enable_hobo_export_for_system_roles(apps, schema_editor):
    RoleDefinition = apps.get_model("accounts", "RoleDefinition")
    RolePermissionPolicy = apps.get_model("accounts", "RolePermissionPolicy")
    for role in RoleDefinition.objects.filter(key__in=["ops", "developer"]):
        policy = RolePermissionPolicy.objects.filter(role_id=role.id).first()
        if policy:
            policy.action_hobo_ledger_export = True
            policy.save(update_fields=["action_hobo_ledger_export"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_rolepermissionpolicy_action_release_entry_export"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolepermissionpolicy",
            name="action_hobo_ledger_export",
            field=models.BooleanField(
                default=False, verbose_name="导出 HOBO 需求登记 Excel"
            ),
        ),
        migrations.RunPython(_enable_hobo_export_for_system_roles, migrations.RunPython.noop),
    ]
