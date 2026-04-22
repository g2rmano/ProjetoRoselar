from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class EventType(models.TextChoices):
    """Tipos de evento no calendário."""
    DELIVERY = "DELIVERY", "Entrega"
    QUOTE_FOLLOWUP = "QUOTE_FOLLOWUP", "Follow-up de Orçamento"
    ARCHITECT_PAYMENT = "ARCHITECT_PAYMENT", "Pagamento Arquiteto"
    CUSTOM = "CUSTOM", "Personalizado"


class EventStatus(models.TextChoices):
    """Status do evento."""
    PENDING = "PENDING", "Pendente"
    DONE = "DONE", "Concluído"
    OVERDUE = "OVERDUE", "Atrasado"
    CANCELED = "CANCELED", "Cancelado"


class ReminderStatus(models.TextChoices):
    """Status dos lembretes."""
    SCHEDULED = "SCHEDULED", "Agendado"
    SENT = "SENT", "Enviado"
    DISMISSED = "DISMISSED", "Dispensado"


# ---------------------------------------------------------------------------
# Tags / Etiquetas (estilo Trello)
# ---------------------------------------------------------------------------

class TagColor(models.TextChoices):
    """Paleta de cores para tags — inspirada no Trello."""
    GREEN = "#61bd4f", "Verde"
    YELLOW = "#f2d600", "Amarelo"
    ORANGE = "#ff9f1a", "Laranja"
    RED = "#eb5a46", "Vermelho"
    PURPLE = "#c377e0", "Roxo"
    BLUE = "#0079bf", "Azul"
    SKY = "#00c2e0", "Celeste"
    LIME = "#51e898", "Lima"
    PINK = "#ff78cb", "Rosa"
    BLACK = "#344563", "Escuro"


class EventTag(models.Model):
    """
    Etiqueta / label reutilizável, criada pelo usuário.
    Cada tag tem nome e cor (paleta Trello).
    """

    name = models.CharField(max_length=60, help_text="Nome da etiqueta")
    color = models.CharField(
        max_length=7,
        choices=TagColor.choices,
        default=TagColor.GREEN,
        help_text="Cor da etiqueta",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tags",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Etiqueta"
        verbose_name_plural = "Etiquetas"

    def __str__(self) -> str:
        return self.name

    @property
    def text_color(self) -> str:
        """Retorna cor do texto (branco ou preto) com base na luminância."""
        dark_colors = {TagColor.BLUE, TagColor.RED, TagColor.PURPLE, TagColor.BLACK}
        return "#fff" if self.color in dark_colors else "#333"


class CalendarEvent(models.Model):
    """
    Evento genérico do calendário.
    Pode representar entregas, follow-ups de orçamentos ou eventos personalizados.
    """

    title = models.CharField(max_length=200, help_text="Título do evento")
    description = models.TextField(blank=True, help_text="Descrição detalhada")

    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        default=EventType.CUSTOM,
    )
    status = models.CharField(
        max_length=20,
        choices=EventStatus.choices,
        default=EventStatus.PENDING,
    )

    event_date = models.DateField(help_text="Data do evento")
    event_time = models.TimeField(null=True, blank=True, help_text="Horário (opcional)")

    # Quem é responsável / atribuído
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_events",
        help_text="Vendedor responsável",
    )

    # Vínculos opcionais
    quote = models.ForeignKey(
        "sales.Quote",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="calendar_events",
        help_text="Orçamento vinculado (se aplicável)",
    )
    order = models.ForeignKey(
        "sales.Order",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="calendar_events",
        help_text="Pedido vinculado (se aplicável)",
    )
    customer = models.ForeignKey(
        "core.Customer",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="calendar_events",
        help_text="Cliente vinculado (se aplicável)",
    )

    # Tags / etiquetas (estilo Trello)
    tags = models.ManyToManyField(
        EventTag,
        blank=True,
        related_name="events",
        help_text="Etiquetas do evento",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event_date", "event_time"]
        indexes = [
            models.Index(fields=["event_date"]),
            models.Index(fields=["event_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["assigned_to"]),
        ]
        verbose_name = "Evento do Calendário"
        verbose_name_plural = "Eventos do Calendário"

    def __str__(self) -> str:
        return f"{self.title} - {self.event_date:%d/%m/%Y}"

    @property
    def is_overdue(self) -> bool:
        """Verifica se o evento está atrasado."""
        if self.status in (EventStatus.DONE, EventStatus.CANCELED):
            return False
        return self.event_date < timezone.localdate()

    @property
    def days_until(self) -> int:
        """Dias até o evento (negativo = atrasado)."""
        return (self.event_date - timezone.localdate()).days

    def mark_done(self):
        """Marca evento como concluído."""
        self.status = EventStatus.DONE
        self.save(update_fields=["status", "updated_at"])

    def mark_canceled(self):
        """Marca evento como cancelado."""
        self.status = EventStatus.CANCELED
        self.save(update_fields=["status", "updated_at"])


class Reminder(models.Model):
    """
    Lembrete associado a um evento do calendário.
    Cada evento pode ter múltiplos lembretes (ex: 7 dias antes, 3 dias antes, no dia).
    """

    event = models.ForeignKey(
        CalendarEvent,
        on_delete=models.CASCADE,
        related_name="reminders",
    )

    remind_date = models.DateField(help_text="Data para enviar o lembrete")

    status = models.CharField(
        max_length=20,
        choices=ReminderStatus.choices,
        default=ReminderStatus.SCHEDULED,
    )

    message = models.CharField(
        max_length=300,
        blank=True,
        help_text="Mensagem personalizada do lembrete",
    )

    read = models.BooleanField(default=False, help_text="Se o usuário já leu o lembrete")
    read_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["remind_date"]
        indexes = [
            models.Index(fields=["remind_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["read"]),
        ]
        verbose_name = "Lembrete"
        verbose_name_plural = "Lembretes"

    def __str__(self) -> str:
        return f"Lembrete: {self.event.title} em {self.remind_date:%d/%m/%Y}"

    def mark_as_read(self):
        """Marca o lembrete como lido."""
        self.read = True
        self.read_at = timezone.now()
        self.save(update_fields=["read", "read_at"])

    def dismiss(self):
        """Dispensa o lembrete."""
        self.status = ReminderStatus.DISMISSED
        self.read = True
        self.read_at = timezone.now()
        self.save(update_fields=["status", "read", "read_at"])


class EventAttachment(models.Model):
    """Anexo de arquivo armazenado como BLOB no banco de dados."""

    event = models.ForeignKey(
        CalendarEvent,
        on_delete=models.CASCADE,
        related_name="attachments",
    )

    filename = models.CharField(max_length=255, help_text="Nome original do arquivo")
    content_type = models.CharField(max_length=100, help_text="MIME type do arquivo")
    file_data = models.BinaryField(help_text="Conteúdo do arquivo em bytes")
    file_size = models.PositiveIntegerField(default=0, help_text="Tamanho em bytes")

    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_attachments",
    )

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Anexo"
        verbose_name_plural = "Anexos"

    def __str__(self) -> str:
        return f"{self.filename} ({self.event.title})"

    @property
    def file_size_display(self) -> str:
        """Retorna tamanho formatado (KB / MB)."""
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{self.file_size / (1024 * 1024):.1f} MB"

