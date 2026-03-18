from django.contrib import admin
from .models import CalendarEvent, EventAttachment, EventTag, Reminder
from core.admin_helpers import AdminOnly, SellerAccess


class ReminderInline(admin.TabularInline):
    model = Reminder
    extra = 0
    fields = ("remind_date", "message", "status", "read")


@admin.register(EventTag)
class EventTagAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("name", "color")
    fields = ("name", "color", "created_by")


@admin.register(CalendarEvent)
class CalendarEventAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("title", "event_type", "event_date", "status", "assigned_to")
    list_filter = ("event_type", "status")
    search_fields = ("title", "customer__name")
    filter_horizontal = ("tags",)
    inlines = [ReminderInline]
    fields = (
        "title", "description", "event_type", "status", "assigned_to",
        "event_date", "event_time",
        "customer", "quote", "order", "tags",
    )


@admin.register(Reminder)
class ReminderAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("event", "remind_date", "status", "read")
    list_filter = ("status", "read")
    fields = ("event", "remind_date", "message", "status", "read")
