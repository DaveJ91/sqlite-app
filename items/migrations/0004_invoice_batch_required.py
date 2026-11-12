import django.db.models.deletion
from django.db import migrations, models


def clear_invoices(apps, schema_editor):
    apps.get_model("items", "Invoice").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0003_batch_alter_invoice_options_and_more"),
    ]

    operations = [
        migrations.RunPython(clear_invoices, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="invoice",
            name="batch",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="invoices",
                to="items.batch",
            ),
        ),
    ]
