from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0019_migrate_open_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="quote",
            name="price_increase_percent",
            field=models.DecimalField(
                decimal_places=1,
                default=Decimal("0.0"),
                help_text="Acréscimo percentual sobre o subtotal, repassado ao cliente.",
                max_digits=5,
                verbose_name="Ajuste de Preço (%)",
            ),
        ),
    ]
