from django.contrib import admin
from .models import CalendarEvent, EventAttachment, EventTag, Reminder


class ReminderInline(admin.TabularInline):
    model = Reminder
    extra = 0
    readonly_fields = ("created_at",)


class EventAttachmentInline(admin.TabularInline):
    model = EventAttachment
    extra = 0
    readonly_fields = ("filename", "content_type", "file_size", "uploaded_by", "uploaded_at")
    fields = ("filename", "content_type", "file_size", "uploaded_by", "uploaded_at")


@admin.register(EventTag)
class EventTagAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "created_by")
    list_filter = ("color",)
    search_fields = ("name",)


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("title", "event_type", "event_date", "status", "assigned_to", "customer")
    list_filter = ("event_type", "status", "assigned_to", "tags")
    search_fields = ("title", "description", "customer__name")
    date_hierarchy = "event_date"
    filter_horizontal = ("tags",)
    inlines = [ReminderInline, EventAttachmentInline]


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("event", "remind_date", "status", "read", "message")
    list_filter = ("status", "read", "remind_date")
    search_fields = ("message", "event__title")
