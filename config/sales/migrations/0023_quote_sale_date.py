from django.db import migrations, models
from django.db.models import Min
from django.utils import timezone

SOLD_STATUSES = ("CONVERTED", "POS_VENDA")


def backfill_sale_date(apps, schema_editor):
    """Preenche sale_date dos orçamentos já vendidos.

    Usa a data de criação do primeiro pedido gerado (momento real da
    conversão); se não houver pedido (ex.: pedidos cancelados/removidos
    com status mantido), cai para a data do orçamento.
    """
    Quote = apps.get_model("sales", "Quote")
    qs = (
        Quote.objects.filter(status__in=SOLD_STATUSES, sale_date__isnull=True)
        .annotate(first_order_at=Min("orders__created_at"))
        .only("id", "quote_date")
    )
    to_update = []
    for quote in qs:
        dt = quote.first_order_at
        if dt is not None:
            if timezone.is_aware(dt):
                dt = timezone.localtime(dt)
            quote.sale_date = dt.date()
        else:
            quote.sale_date = quote.quote_date
        to_update.append(quote)
    Quote.objects.bulk_update(to_update, ["sale_date"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0022_quote_total_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="quote",
            name="sale_date",
            field=models.DateField(
                blank=True,
                help_text="Data em que o orçamento foi convertido em pedido.",
                null=True,
                verbose_name="Data da Venda",
            ),
        ),
        migrations.AddIndex(
            model_name="quote",
            index=models.Index(fields=["sale_date"], name="sales_quote_sale_date_idx"),
        ),
        migrations.RunPython(backfill_sale_date, migrations.RunPython.noop),
    ]
