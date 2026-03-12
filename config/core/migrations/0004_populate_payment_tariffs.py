# Generated migration to populate payment tariffs with example data

from django.db import migrations


def create_example_tariffs(apps, schema_editor):
    """Create example payment tariffs."""
    PaymentTariff = apps.get_model('core', 'PaymentTariff')
    
    # Cash - à vista (0%)
    PaymentTariff.objects.get_or_create(
        payment_type='CASH',
        installments=1,
        defaults={'fee_percent': 0.0}
    )
    
    # PIX - à vista (0%)
    PaymentTariff.objects.get_or_create(
        payment_type='PIX',
        installments=1,
        defaults={'fee_percent': 0.0}
    )
    
    # Cheque - à vista (0%)
    PaymentTariff.objects.get_or_create(
        payment_type='CHEQUE',
        installments=1,
        defaults={'fee_percent': 0.0}
    )
    
    # Credit Card - up to 12x with increasing fees
    credit_card_tariffs = [
        (1, 0.0),    # à vista - sem juros
        (2, 2.5),    # 2x - 2.5%
        (3, 3.8),    # 3x - 3.8%
        (4, 5.1),    # 4x - 5.1%
        (5, 6.4),    # 5x - 6.4%
        (6, 7.7),    # 6x - 7.7%
        (7, 9.0),    # 7x - 9.0%
        (8, 10.3),   # 8x - 10.3%
        (9, 11.6),   # 9x - 11.6%
        (10, 12.9),  # 10x - 12.9%
        (11, 14.2),  # 11x - 14.2%
        (12, 15.5),  # 12x - 15.5%
    ]
    
    for installments, fee in credit_card_tariffs:
        PaymentTariff.objects.get_or_create(
            payment_type='CREDIT_CARD',
            installments=installments,
            defaults={'fee_percent': fee}
        )
    
    # Boleto - up to 6x with fees
    boleto_tariffs = [
        (1, 0.0),    # à vista - sem juros
        (2, 2.0),    # 2x - 2.0%
        (3, 3.5),    # 3x - 3.5%
        (4, 5.0),    # 4x - 5.0%
        (5, 6.5),    # 5x - 6.5%
        (6, 8.0),    # 6x - 8.0%
    ]
    
    for installments, fee in boleto_tariffs:
        PaymentTariff.objects.get_or_create(
            payment_type='BOLETO',
            installments=installments,
            defaults={'fee_percent': fee}
        )


def remove_example_tariffs(apps, schema_editor):
    """Remove example tariffs."""
    PaymentTariff = apps.get_model('core', 'PaymentTariff')
    PaymentTariff.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_paymenttariff'),
    ]

    operations = [
        migrations.RunPython(create_example_tariffs, remove_example_tariffs),
    ]
