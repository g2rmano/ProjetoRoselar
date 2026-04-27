"""Add split payment fields to Quote (second payment method support)."""

from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0013_quote_architect'),
    ]

    operations = [
        migrations.AddField(
            model_name='quote',
            name='payment_type_2',
            field=models.CharField(
                blank=True,
                max_length=20,
                verbose_name='Tipo de Pagamento 2',
                help_text='Segundo método (pagamento dividido entre dois meios)',
            ),
        ),
        migrations.AddField(
            model_name='quote',
            name='payment_installments_2',
            field=models.PositiveIntegerField(
                default=1,
                verbose_name='Parcelas 2',
            ),
        ),
        migrations.AddField(
            model_name='quote',
            name='payment_fee_percent_2',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                max_digits=5,
                verbose_name='Taxa de Pagamento 2 (%)',
            ),
        ),
        migrations.AddField(
            model_name='quote',
            name='payment_split_amount',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Quanto do total vai ao primeiro método; o restante vai ao segundo.',
                max_digits=12,
                null=True,
                verbose_name='Valor no Pagamento 1 (R$)',
            ),
        ),
    ]
