"""Reintroduz escalonamento de taxa no cartão de crédito.

Regra pedida pelo cliente (Gustavo, 11/07/2026):
    1x        -> 4,00%
    2x        -> 3,50%
    3x a 6x   -> 3,00%

Parcelas acima de 6x não são alteradas (mantêm o valor atual do banco).
Obs.: CHEQUE usa a tabela do CREDIT_CARD como referência no simulador,
portanto herda estas taxas.
"""

from decimal import Decimal

from django.db import migrations


CARD_TIERS = {
    1: Decimal("4.00"),
    2: Decimal("3.50"),
    3: Decimal("3.00"),
    4: Decimal("3.00"),
    5: Decimal("3.00"),
    6: Decimal("3.00"),
}


def apply_tiers(apps, schema_editor):
    PaymentTariff = apps.get_model("core", "PaymentTariff")
    for installments, fee in CARD_TIERS.items():
        PaymentTariff.objects.update_or_create(
            payment_type="CREDIT_CARD",
            installments=installments,
            defaults={"fee_percent": fee},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_architect_pix_optional"),
    ]

    operations = [
        migrations.RunPython(apply_tiers, migrations.RunPython.noop),
    ]
