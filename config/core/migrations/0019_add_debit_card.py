from django.db import migrations, models


def add_debit_card_tariff(apps, schema_editor):
    PaymentTariff = apps.get_model('core', 'PaymentTariff')
    PaymentTariff.objects.get_or_create(
        payment_type='DEBIT_CARD',
        installments=1,
        defaults={'fee_percent': 0.00},
    )


def remove_debit_card_tariff(apps, schema_editor):
    PaymentTariff = apps.get_model('core', 'PaymentTariff')
    PaymentTariff.objects.filter(payment_type='DEBIT_CARD').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_remove_margin_limit_combine_into_total_margin'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymenttariff',
            name='payment_type',
            field=models.CharField(
                choices=[
                    ('CASH', 'Dinheiro'),
                    ('PIX', 'PIX'),
                    ('DEBIT_CARD', 'Cartão de Débito'),
                    ('CREDIT_CARD', 'Cartão de Crédito'),
                    ('CHEQUE', 'Cheque'),
                    ('BOLETO', 'Boleto'),
                ],
                help_text='Tipo do método de pagamento',
                max_length=20,
                verbose_name='Tipo de Pagamento',
            ),
        ),
        migrations.RunPython(add_debit_card_tariff, remove_debit_card_tariff),
    ]
