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

    email = models.EmailField(
        unique=True,
        help_text="E-mail único do fornecedor (identificador principal)"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"