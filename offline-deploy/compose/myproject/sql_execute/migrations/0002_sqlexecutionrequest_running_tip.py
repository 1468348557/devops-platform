from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sql_execute", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sqlexecutionrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "待审批"),
                    ("approved", "审批通过"),
                    ("running", "执行中"),
                    ("rejected", "审批拒绝"),
                    ("success", "执行成功"),
                    ("failed", "执行失败"),
                ],
                default="pending",
                max_length=16,
                verbose_name="状态",
            ),
        ),
        migrations.AddField(
            model_name="sqlexecutionrequest",
            name="execution_tip",
            field=models.CharField(
                blank=True,
                default="",
                max_length=255,
                verbose_name="执行进度提示",
            ),
        ),
    ]
