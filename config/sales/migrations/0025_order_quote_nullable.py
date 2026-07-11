import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0024_merge_20260709_1702"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="quote",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="sales.quote",
                verbose_name="Orçamento",
            ),
        ),
    ]
