from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def quote_item_tmp_path(instance: "QuoteItemImage", filename: str) -> str:
    # organização por orçamento e item
    return f"tmp/quotes/{instance.item.quote.number}/items/{instance.item_id}/{filename}"


def validate_discount_percent(value: Decimal):
    if value is None:
        return
    if value < 0 or value > 100:
        raise ValidationError("Desconto deve estar entre 0 e 100.")


class QuoteStatus(models.TextChoices):
    DRAFT = "DRAFT", "Rascunho"
    SENT = "SENT", "Enviado"
    APPROVED = "APPROVED", "Aprovado"
    CONVERTED = "CONVERTED", "Convertido em Pedido"
    CANCELED = "CANCELED", "Cancelado"


class Quote(models.Model):
    number = models.CharField(max_length=20, unique=True)

    customer = models.ForeignKey(
        "core.Customer",
        on_delete=models.PROTECT,
        related_name="quotes",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="quotes",
    )

    status = models.CharField(max_length=12, choices=QuoteStatus.choices, default=QuoteStatus.DRAFT)

    quote_date = models.DateField(default=timezone.localdate)
    freight_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # desconto
    discount_percent = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[validate_discount_percent],
    )
    discount_authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authorized_quote_discounts",
    )
    discount_authorized_at = models.DateTimeField(null=True, blank=True)

    # condição/pagamento (texto, pois varia muito)
    payment_description = models.CharField(max_length=200, blank=True)

    # snapshots (opcional, útil para relatórios e evitar recomputar)
    total_value_snapshot = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["number"]),
            models.Index(fields=["quote_date"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"Orçamento {self.number}"

    @property
    def has_orders(self) -> bool:
        return self.orders.exists()



class QuoteItem(models.Model):
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="items")

    # fornecedor opcional no orçamento, mas NECESSÁRIO para gerar pedido por fornecedor
    supplier = models.ForeignKey(
        "core.Supplier",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="quote_items",
    )

    product_name = models.CharField(max_length=160)
    description = models.TextField(blank=True)

    quantity = models.PositiveIntegerField(default=1)
    unit_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # condição do item (se precisar)
    condition_text = models.CharField(max_length=200, blank=True)

    # % arquiteto: armazenar, mas NÃO exibir no orçamento/pedido final (regra sua)
    architect_percent = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0.000"))

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["quote"]),
            models.Index(fields=["supplier"]),
        ]

    def __str__(self) -> str:
        return f"{self.product_name} ({self.quote.number})"

    @property
    def line_total(self) -> Decimal:
        return (self.unit_value or Decimal("0.00")) * Decimal(self.quantity or 0)


class QuoteItemImage(models.Model):
    item = models.ForeignKey(QuoteItem, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to=quote_item_tmp_path)
    caption = models.CharField(max_length=120, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)  # TTL padrão
        super().save(*args, **kwargs)



class OrderStatus(models.TextChoices):
    OPEN = "OPEN", "Aberto"
    SENT = "SENT", "Enviado"
    DONE = "DONE", "Concluído"
    CANCELED = "CANCELED", "Cancelado"


class Order(models.Model):
    number = models.CharField(max_length=20)  # igual ao Quote.number (não unique por permitir vários pedidos)
    quote = models.ForeignKey(Quote, on_delete=models.PROTECT, related_name="orders")

    supplier = models.ForeignKey(
        "core.Supplier",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    is_total_conference = models.BooleanField(default=False)

    status = models.CharField(max_length=10, choices=OrderStatus.choices, default=OrderStatus.OPEN)

    purchase_condition_text = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["number"]),
            models.Index(fields=["quote"]),
            models.Index(fields=["supplier"]),
        ]
        constraints = [
            # 1 orçamento -> no máximo 1 "pedido total"
            models.UniqueConstraint(
                fields=["quote"],
                condition=models.Q(is_total_conference=True),
                name="uniq_total_order_per_quote",
            ),
            # se for pedido total, supplier deve ser null
            models.CheckConstraint(
                condition=~(models.Q(is_total_conference=True) & models.Q(supplier__isnull=False)),
                name="total_order_has_no_supplier",
            ),
        ]

    def clean(self):
        # validação extra: pedido normal (não total) deve ter supplier
        if not self.is_total_conference and self.supplier_id is None:
            raise ValidationError("Pedido por fornecedor precisa de fornecedor.")
        if self.is_total_conference and self.supplier_id is not None:
            raise ValidationError("Pedido total não pode ter fornecedor.")

    def __str__(self) -> str:
        return f"OC {self.number}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")

    product_name = models.CharField(max_length=160)
    description = models.TextField(blank=True)

    quantity = models.PositiveIntegerField(default=1)

    # custo de compra (interno). Se você não quer isso agora, pode remover.
    purchase_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # link opcional ao item do orçamento (útil para rastrear origem)
    quote_item = models.ForeignKey(
        QuoteItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_items",
    )

    def __str__(self) -> str:
        return f"{self.product_name} ({self.order.number})"
