"""Update payment tariffs with real interest rates and extend to 12x for all card/boleto types."""

from django.db import migrations


def update_tariffs(apps, schema_editor):
    PaymentTariff = apps.get_model('core', 'PaymentTariff')

    # New interest table (from business rules)
    # Parcelas -> Juros %
    interest_table = {
        1:  0.00,
        2:  5.32,
        3:  6.54,
        4:  7.08,
        5:  8.13,
        6:  8.58,
        7:  9.87,
        8:  10.50,
        9:  11.41,
        10: 11.92,
        11: 12.79,
        12: 13.30,
    }

    # CASH, PIX, CHEQUE — always 1x, 0%
    for pt in ('CASH', 'PIX', 'CHEQUE'):
        PaymentTariff.objects.update_or_create(
            payment_type=pt, installments=1,
            defaults={'fee_percent': 0.00},
        )
        # Remove any extra installments that may exist
        PaymentTariff.objects.filter(payment_type=pt, installments__gt=1).delete()

    # CREDIT_CARD and BOLETO — 1-12x with new rates
    for pt in ('CREDIT_CARD', 'BOLETO'):
        for inst, fee in interest_table.items():
            PaymentTariff.objects.update_or_create(
                payment_type=pt, installments=inst,
                defaults={'fee_percent': fee},
            )
        # Remove any installments > 12
        PaymentTariff.objects.filter(payment_type=pt, installments__gt=12).delete()

    # Update SalesMarginConfig to 10%
    SalesMarginConfig = apps.get_model('core', 'SalesMarginConfig')
    obj, _ = SalesMarginConfig.objects.get_or_create(pk=1)
    obj.total_margin = 10.0
    obj.save(update_fields=['total_margin'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_new_pricing_rules'),
    ]

    operations = [
        migrations.RunPython(update_tariffs, migrations.RunPython.noop),
    ]
