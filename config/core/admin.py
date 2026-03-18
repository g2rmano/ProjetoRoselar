from django.contrib import admin
from .models import (
    Customer, Supplier, ShippingCompany,
    PaymentTariff, ArchitectCommission, SalesMarginConfig,
    Notification, AuditLog, SalesGoal,
    CommunicationHistory,
    QuoteTemplate, QuoteTemplateItem,
)


from .admin_helpers import _is_admin, AdminOnly, SellerAccess


@admin.register(Customer)
class CustomerAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("name", "phone", "email")
    search_fields = ("name", "phone", "email")
    actions = ["delete_selected"]


@admin.register(Supplier)
class SupplierAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("supplier_number", "name", "phone")
    search_fields = ("name",)
    readonly_fields = ("supplier_number",)
    fields = ("name", "email", "phone", "notes")
    actions = ["delete_selected"]


@admin.register(ShippingCompany)
class ShippingCompanyAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("name", "cnpj", "phone", "is_active")
    search_fields = ("name", "cnpj")
    list_filter = ("is_active",)
    fields = ("name", "cnpj", "phone", "email", "contact_person", "address", "is_active", "notes")
    actions = ["delete_selected"]


@admin.register(PaymentTariff)
class PaymentTariffAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("payment_type", "installments", "fee_percent")
    list_editable = ("fee_percent",)
    ordering = ("payment_type", "installments")
    fields = ("payment_type", "installments", "fee_percent")


@admin.register(ArchitectCommission)
class ArchitectCommissionAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("commission_percent", "updated_at")
    fields = ("commission_percent",)

    def has_add_permission(self, request):
        if not _is_admin(request.user):
            return False
        return not ArchitectCommission.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SalesMarginConfig)
class SalesMarginConfigAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("total_margin", "min_commission", "max_commission")
    fields = ("total_margin", "min_commission", "max_commission")

    def has_add_permission(self, request):
        if not _is_admin(request.user):
            return False
        return not SalesMarginConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("recipient", "title", "read", "created_at")
    list_filter = ("read",)
    search_fields = ("title", "recipient__username")
    readonly_fields = ("created_at",)


@admin.register(AuditLog)
class AuditLogAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("user", "action", "object_type", "created_at")
    list_filter = ("action",)
    search_fields = ("description", "user__username")
    readonly_fields = ("user", "action", "description", "object_type", "object_id", "ip_address", "extra_data", "created_at")

    def has_add_permission(self, request):              return False
    def has_change_permission(self, request, obj=None): return False


@admin.register(SalesGoal)
class SalesGoalAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("goal_type", "seller", "period", "target_value")
    list_filter = ("goal_type",)
    fields = ("goal_type", "seller", "period", "period_start", "period_end", "target_value", "target_quantity")


@admin.register(CommunicationHistory)
class CommunicationHistoryAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("customer", "channel", "created_by", "created_at")
    list_filter = ("channel",)
    search_fields = ("summary", "customer__name")
    readonly_fields = ("created_at",)


class QuoteTemplateItemInline(admin.TabularInline):
    model = QuoteTemplateItem
    extra = 1


@admin.register(QuoteTemplate)
class QuoteTemplateAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("name", "created_by")
    search_fields = ("name",)
    readonly_fields = ("created_at",)
    inlines = [QuoteTemplateItemInline]
