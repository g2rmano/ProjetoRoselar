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


class FreightResponsible(models.TextChoices):
    """Responsável pelo pagamento do frete."""
    STORE = "STORE", "Loja"
    CUSTOMER = "CUSTOMER", "Cliente"
    CARRIER = "CARRIER", "Transportadora"


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
    
    # prazo de entrega
    delivery_deadline = models.DateField(
        null=True,
        blank=True,
        help_text="Prazo de entrega previsto"
    )
    
    # frete
    freight_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    freight_responsible = models.CharField(
        max_length=10,
        choices=FreightResponsible.choices,
        default=FreightResponsible.CUSTOMER,
        help_text="Quem paga o frete"
    )
    shipping_company = models.ForeignKey(
        "core.ShippingCompany",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="quotes",
        help_text="Transportadora responsável (se aplicável)"
    )
    shipping_payment_method = models.CharField(
        max_length=200,
        blank=True,
        help_text="Método de pagamento selecionado da transportadora"
    )

    # desconto
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal("0.0"),
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

    # arquiteto
    has_architect = models.BooleanField(
        default=False,
        help_text="Cliente possui arquiteto?"
    )

    # condição/pagamento
    payment_type = models.CharField(
        max_length=20,
        blank=True,
        help_text="Tipo de pagamento (dinheiro, pix, cartão, etc.)"
    )
    payment_installments = models.PositiveIntegerField(
        default=1,
        help_text="Número de parcelas (1 = à vista)"
    )
    payment_fee_percent = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal("0.0"),
        help_text="Taxa aplicada conforme parcelas"
    )

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
    
    def get_payment_description(self) -> str:
        """Retorna descrição formatada do pagamento."""
        from core.models import PaymentMethodType
        
        if self.payment_type:
            type_display = dict(PaymentMethodType.choices).get(self.payment_type, self.payment_type)
            if self.payment_installments == 1:
                return f"{type_display} - À vista"
            else:
                return f"{type_display} - {self.payment_installments}x"
        return "Não definido"
    
    def calculate_subtotal(self) -> Decimal:
        """Calcula subtotal dos itens sem desconto e sem frete."""
        subtotal = sum(item.line_total for item in self.items.all())
        return Decimal(str(subtotal))
    
    def calculate_total_with_freight_and_discount(self) -> Decimal:
        """Calcula total com frete e desconto, mas SEM taxa de pagamento."""
        subtotal = self.calculate_subtotal()
        with_freight = subtotal + (self.freight_value or Decimal("0.00"))
        discount_value = with_freight * (self.discount_percent or Decimal("0.000")) / Decimal("100")
        return with_freight - discount_value
    
    def calculate_payment_fee_value(self) -> Decimal:
        """Calcula o valor da taxa de pagamento."""
        base_total = self.calculate_total_with_freight_and_discount()
        fee_value = base_total * (self.payment_fee_percent or Decimal("0.000")) / Decimal("100")
        return fee_value
    
    def calculate_final_total(self) -> Decimal:
        """Calcula o total final incluindo taxa de pagamento."""
        base_total = self.calculate_total_with_freight_and_discount()
        fee_value = self.calculate_payment_fee_value()
        return base_total + fee_value



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
    architect_percent = models.DecimalField(
        max_digits=4, 
        decimal_places=1, 
        null=True, 
        blank=True, 
        default=Decimal("0.0")
    )

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
    
    @property
    def line_total(self) -> Decimal:
        """Calculate the total for this line item."""
        return (self.purchase_unit_cost or Decimal("0.00")) * Decimal(self.quantity or 0)
