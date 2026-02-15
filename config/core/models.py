from django.db import models
from django.db.models import Q
from .validador import validate_cpf, validate_cnpj


class Customer(models.Model):
    name = models.CharField(max_length=120)

    cpf = models.CharField(
        max_length=14,
        blank=True,
        validators=[validate_cpf],
        help_text="CPF válido (com ou sem máscara)",
    )

    cnpj = models.CharField(
        max_length=18,
        blank=True,
        validators=[validate_cnpj],
        help_text="CNPJ válido (com ou sem máscara)",
    )

    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cpf"],
                name="uniq_customer_cpf",
                condition=~Q(cpf=""),
            ),
            models.UniqueConstraint(
                fields=["cnpj"],
                name="uniq_customer_cnpj",
                condition=~Q(cnpj=""),
            ),
            models.CheckConstraint(
                condition=~(Q(cpf="") & Q(cnpj="")),
                name="cpf_or_cnpj_required",
            ),
        ]

    def __str__(self):
        if self.cpf:
            return f"{self.name} (PF - {self.cpf})"
        elif self.cnpj:
            return f"{self.name} (PJ - {self.cnpj})"
        return self.name


class Supplier(models.Model):
    name = models.CharField(max_length=120)
    supplier_number = models.CharField(
        max_length=50,
        unique=True,
        help_text="Número/código do fornecedor"
    )

    email = models.EmailField(
        unique=True,
        help_text="E-mail único do fornecedor (identificador principal)"
    )
    
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True, help_text="Informações adicionais sobre o fornecedor")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.supplier_number})"


class SupplierPaymentOption(models.Model):
    """Opções de pagamento personalizadas por fornecedor."""
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.CASCADE,
        related_name="payment_options"
    )
    
    description = models.CharField(
        max_length=200,
        help_text="Ex: '30/60/90 dias', 'À vista com 5% desconto', etc."
    )
    
    days_to_pay = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Prazo em dias (opcional)"
    )
    
    is_default = models.BooleanField(
        default=False,
        help_text="Marcar como opção padrão para este fornecedor"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["-is_default", "days_to_pay"]
    
    def __str__(self) -> str:
        return f"{self.supplier.name} - {self.description}"


class ShippingCompany(models.Model):
    """Cadastro de transportadoras."""
    name = models.CharField(max_length=120, help_text="Nome da transportadora")
    
    cnpj = models.CharField(
        max_length=18,
        blank=True,
        validators=[validate_cnpj],
        help_text="CNPJ da transportadora (opcional)"
    )
    
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    contact_person = models.CharField(
        max_length=100,
        blank=True,
        help_text="Pessoa de contato na transportadora"
    )
    
    address = models.TextField(blank=True, help_text="Endereço completo")
    notes = models.TextField(blank=True, help_text="Observações")
    
    # Payment methods available for this shipping company
    payment_methods = models.TextField(
        blank=True,
        help_text="Métodos de pagamento disponíveis (um por linha)"
    )
    
    is_active = models.BooleanField(default=True, help_text="Transportadora ativa")
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["name"]
        verbose_name = "Transportadora"
        verbose_name_plural = "Transportadoras"
    
    def __str__(self) -> str:
        return self.name


class PaymentMethodType(models.TextChoices):
    """Tipos de métodos de pagamento fixos."""
    CASH = "CASH", "Dinheiro"
    PIX = "PIX", "PIX"
    CREDIT_CARD = "CREDIT_CARD", "Cartão de Crédito"
    CHEQUE = "CHEQUE", "Cheque"
    BOLETO = "BOLETO", "Boleto"


class PaymentTariff(models.Model):
    """Tarifas configuráveis para métodos de pagamento parcelados."""
    payment_type = models.CharField(
        max_length=20,
        choices=PaymentMethodType.choices,
        help_text="Tipo do método de pagamento"
    )
    
    installments = models.PositiveIntegerField(
        help_text="Número de parcelas (1 = à vista)"
    )
    
    fee_percent = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        default=0,
        help_text="Porcentagem de acréscimo (ex: 2.5 para 2.5%)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ["payment_type", "installments"]
        verbose_name = "Tarifa de Pagamento"
        verbose_name_plural = "Tarifas de Pagamento"
        unique_together = [["payment_type", "installments"]]
    
    def __str__(self) -> str:
        type_display = dict(PaymentMethodType.choices).get(self.payment_type, self.payment_type)
        if self.installments == 1:
            return f"{type_display} - À vista ({self.fee_percent}%)"
        return f"{type_display} - {self.installments}x ({self.fee_percent}%)"
    
    @classmethod
    def get_fee(cls, payment_type, installments):
        """Retorna a taxa para um método e parcelas específicos."""
        try:
            tariff = cls.objects.get(payment_type=payment_type, installments=installments)
            return tariff.fee_percent
        except cls.DoesNotExist:
            return 0


class ArchitectCommission(models.Model):
    """Configuração global para comissão de arquiteto (singleton)."""
    commission_percent = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        default=10.0,
        help_text="Porcentagem de comissão do arquiteto (ex: 10.0 para 10%)"
    )
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Comissão de Arquiteto"
        verbose_name_plural = "Comissão de Arquiteto"
    
    def __str__(self) -> str:
        return f"Comissão de Arquiteto: {self.commission_percent}%"
    
    def save(self, *args, **kwargs):
        # Força singleton - apenas um registro
        self.pk = 1
        super().save(*args, **kwargs)
    
    @classmethod
    def get_commission(cls):
        """Retorna a comissão configurada."""
        obj, created = cls.objects.get_or_create(pk=1)
        return obj.commission_percent