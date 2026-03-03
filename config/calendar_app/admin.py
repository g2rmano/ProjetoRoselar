from django.contrib import admin
from .models import CalendarEvent, EventAttachment, EventTag, Reminder


def _is_admin(user):
    """True para ADMIN, OWNER ou superusuário Django."""
    return user.is_superuser or getattr(user, "role", None) in ("ADMIN", "OWNER")


class AdminOnly:
    """Somente admins/donos podem visualizar ou modificar este modelo."""
    def has_view_permission(self, request, obj=None):   return _is_admin(request.user)
    def has_add_permission(self, request):              return _is_admin(request.user)
    def has_change_permission(self, request, obj=None): return _is_admin(request.user)
    def has_delete_permission(self, request, obj=None): return _is_admin(request.user)


class SellerAccess:
    """Vendedores podem visualizar/adicionar/editar; somente admins podem excluir."""
    def has_view_permission(self, request, obj=None):   return True
    def has_add_permission(self, request):              return True
    def has_change_permission(self, request, obj=None): return True
    def has_delete_permission(self, request, obj=None): return _is_admin(request.user)


class ReminderInline(admin.TabularInline):
    model = Reminder
    extra = 0
    verbose_name = "Lembrete"
    verbose_name_plural = "Lembretes"
    readonly_fields = ("created_at",)
    fields = ("remind_date", "message", "status", "read", "created_at")


class EventAttachmentInline(admin.TabularInline):
    model = EventAttachment
    extra = 0
    verbose_name = "Anexo"
    verbose_name_plural = "Anexos"
    readonly_fields = ("filename", "content_type", "file_size", "uploaded_by", "uploaded_at")
    fields = ("filename", "content_type", "file_size", "uploaded_by", "uploaded_at")


@admin.register(EventTag)
class EventTagAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("name", "color", "created_by", "created_at")
    list_filter = ("color",)
    search_fields = ("name",)
    readonly_fields = ("created_at",)


@admin.register(CalendarEvent)
class CalendarEventAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("title", "event_type", "event_date", "status", "assigned_to", "customer")
    list_filter = ("event_type", "status", "assigned_to")
    search_fields = ("title", "description", "customer__name", "assigned_to__username")
    date_hierarchy = "event_date"
    filter_horizontal = ("tags",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [ReminderInline, EventAttachmentInline]
    fieldsets = (
        ("Informações do Evento", {
            "fields": ("title", "description", "event_type", "status", "assigned_to"),
        }),
        ("Data e Horário", {
            "fields": ("event_date", "event_time"),
        }),
        ("Vínculos", {
            "fields": ("customer", "quote", "order", "tags"),
        }),
        ("Datas do Sistema", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(Reminder)
class ReminderAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("event", "remind_date", "status", "read", "message")
    list_filter = ("status", "read", "remind_date")
    search_fields = ("message", "event__title", "event__assigned_to__username")
    readonly_fields = ("created_at", "read_at")
    date_hierarchy = "remind_date"
