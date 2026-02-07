from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    SELLER = "SELLER", "Vendedor"
    ADMIN = "ADMIN", "Admin"
    OWNER = "OWNER", "Dono"


class User(AbstractUser):
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.SELLER)

    # metas (pode ficar aqui ou em tabela separada; aqui é mais simples)
    individual_target_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # telefone opcional (útil para envio/agenda)
    phone = models.CharField(max_length=30, blank=True)
