from django.contrib import admin
from .models import (
    Customer, Supplier, SupplierPaymentOption, ShippingCompany,
    PaymentTariff, ArchitectCommission, SalesMarginConfig,
    Notification, AuditLog, SalesGoal,
    CommunicationHistory,
    QuoteTemplate, QuoteTemplateItem,
)


from .admin_helpers import _is_admin, AdminOnly, SellerAccess


@admin.register(Customer)
class CustomerAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("id", "name", "phone", "email", "created_at")
    search_fields = ("name", "phone", "email", "cpf", "cnpj")
    list_filter = ("created_at",)


class SupplierPaymentOptionInline(admin.TabularInline):
    model = SupplierPaymentOption
    extra = 1
    verbose_name = "Forma de Pagamento"
    verbose_name_plural = "Formas de Pagamento"
    fields = ("description", "days_to_pay", "is_default")


@admin.register(Supplier)
class SupplierAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("id", "name", "supplier_number", "email", "phone", "created_at")
    list_display_links = ("id", "name")
    search_fields = ("name", "supplier_number", "email")
    list_filter = ("created_at",)
    inlines = [SupplierPaymentOptionInline]
    fieldsets = (
        ("Informações Básicas", {"fields": ("name", "supplier_number")}),
        ("Contato", {"fields": ("email", "phone")}),
        ("Observações", {"fields": ("notes",)}),
    )


@admin.register(ShippingCompany)
class ShippingCompanyAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("id", "name", "cnpj", "phone", "email", "is_active", "created_at")
    search_fields = ("name", "cnpj", "email", "contact_person")
    list_filter = ("is_active", "created_at")
    fieldsets = (
        ("Informações Básicas", {
            "fields": ("name", "cnpj", "is_active")
        }),
        ("Contato", {
            "fields": ("phone", "email", "contact_person")
        }),
        ("Endereço e Observações", {
            "fields": ("address", "notes")
        }),
    )


@admin.register(PaymentTariff)
class PaymentTariffAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("payment_type", "installments", "fee_percent", "get_description")
    list_filter = ("payment_type",)
    list_editable = ("fee_percent",)
    ordering = ("payment_type", "installments")

    fieldsets = (
        ("Configuração da Tarifa", {
            "fields": ("payment_type", "installments", "fee_percent"),
            "description": "Configure a porcentagem de acréscimo para cada método de pagamento e número de parcelas.",
        }),
    )

    def get_description(self, obj):
        if obj.installments == 1:
            return "À vista"
        return f"{obj.installments}x"
    get_description.short_description = "Descrição"


@admin.register(ArchitectCommission)
class ArchitectCommissionAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("commission_percent", "updated_at")
    
    fieldsets = (
        ("Configuração da Comissão", {
            "fields": ("commission_percent",),
            "description": "Configure a porcentagem fixa de comissão do arquiteto aplicada a todos os itens quando o cliente possui arquiteto."
        }),
    )
    
    def has_add_permission(self, request):
        if not _is_admin(request.user):
            return False
        return not ArchitectCommission.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SalesMarginConfig)
class SalesMarginConfigAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("total_margin", "min_commission", "max_commission", "updated_at")

    fieldsets = (
        ("Margem Total", {
            "fields": ("total_margin",),
            "description": "Margem total (%) que é dividida entre desconto ao cliente, taxa do cartão e comissão do vendedor.",
        }),
        ("Comissão do Vendedor", {
            "fields": ("min_commission", "max_commission"),
            "description": "Faixa de comissão do vendedor. 0% de desconto → comissão máxima. Desconto máximo → comissão mínima.",
        }),
    )

    def has_add_permission(self, request):
        if not _is_admin(request.user):
            return False
        return not SalesMarginConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ── Notificação ──────────────────────────────────────────────────────
@admin.register(Notification)
class NotificationAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("recipient", "notification_type", "title", "read", "created_at")
    list_filter = ("notification_type", "read", "created_at")
    search_fields = ("title", "message", "recipient__username")
    readonly_fields = ("created_at",)
    raw_id_fields = ("recipient",)


# ── Log de Auditoria ─────────────────────────────────────────────────
@admin.register(AuditLog)
class AuditLogAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("user", "action", "object_type", "object_id", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("description", "object_type", "user__username")
    raw_id_fields = ("user",)
    readonly_fields = ("user", "action", "description", "object_type", "object_id", "ip_address", "extra_data", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):              return False
    def has_change_permission(self, request, obj=None): return False


# ── Metas de Vendas ───────────────────────────────────────────────────
@admin.register(SalesGoal)
class SalesGoalAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("goal_type", "seller", "period", "period_start", "period_end", "target_value", "target_quantity")
    list_filter = ("goal_type", "period")
    raw_id_fields = ("seller",)
    fieldsets = (
        ("Tipo e Período", {"fields": ("goal_type", "seller", "period", "period_start", "period_end")}),
        ("Metas", {"fields": ("target_value", "target_quantity")}),
    )


# ── Histórico de Comunicação ─────────────────────────────────────────
@admin.register(CommunicationHistory)
class CommunicationHistoryAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("customer", "channel", "created_by", "created_at")
    list_filter = ("channel", "created_at")
    search_fields = ("summary", "customer__name")
    raw_id_fields = ("customer", "quote", "created_by")
    readonly_fields = ("created_at",)


# ── Modelos de Orçamento ─────────────────────────────────────────────
class QuoteTemplateItemInline(admin.TabularInline):
    model = QuoteTemplateItem
    extra = 1
    verbose_name = "Item do Modelo"
    verbose_name_plural = "Itens do Modelo"


@admin.register(QuoteTemplate)
class QuoteTemplateAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at",)
    inlines = [QuoteTemplateItemInline]
