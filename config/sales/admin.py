from django.contrib import admin
from .models import Quote, QuoteItem, Order, OrderItem, ProposalConfig
from core.admin_helpers import AdminOnly, SellerAccess


class QuoteItemInline(admin.TabularInline):
    model = QuoteItem
    extra = 1
    verbose_name = "Item do Orçamento"
    verbose_name_plural = "Itens do Orçamento"
    fields = ("supplier", "product_name", "quantity", "unit_value")


@admin.register(Quote)
class QuoteAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("number", "customer", "seller", "status", "quote_date", "delivery_weeks", "total_value_snapshot", "created_at")
    list_display_links = ("number",)
    list_filter = ("status", "quote_date", "freight_responsible", "created_at")
    search_fields = ("number", "customer__name", "seller__username")
    date_hierarchy = "quote_date"
    inlines = [QuoteItemInline]
    readonly_fields = ("created_at", "discount_authorized_at", "total_value_snapshot")
    fieldsets = (
        ("Informações Básicas", {
            "fields": ("number", "customer", "seller", "status", "quote_date", "total_value_snapshot"),
        }),
        ("Prazo de Entrega", {
            "fields": ("delivery_weeks",),
        }),
        ("Frete", {
            "fields": ("freight_value", "freight_responsible", "shipping_company", "shipping_payment_method"),
        }),
        ("Desconto", {
            "fields": ("discount_percent", "discount_authorized_by", "discount_authorized_at"),
        }),
        ("Pagamento", {
            "fields": ("payment_type", "payment_installments", "payment_fee_percent"),
        }),
        ("Arquiteto", {
            "fields": ("has_architect",),
        }),
    )


@admin.register(Order)
class OrderAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("number", "quote", "supplier", "status", "is_total_conference", "created_at")
    list_filter = ("status", "is_total_conference", "created_at")
    search_fields = ("number", "quote__number", "supplier__name")
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Identificação", {"fields": ("number", "quote", "supplier", "is_total_conference")}),
        ("Status", {"fields": ("status", "purchase_condition_text", "notes")}),
    )


@admin.register(QuoteItem)
class QuoteItemAdmin(SellerAccess, admin.ModelAdmin):
    list_display = ("id", "quote", "product_name", "supplier", "quantity", "unit_value")
    list_filter = ("quote__status",)
    search_fields = ("product_name", "quote__number", "supplier__name")


@admin.register(OrderItem)
class OrderItemAdmin(AdminOnly, admin.ModelAdmin):
    list_display = ("id", "order", "product_name", "quantity", "purchase_unit_cost")
    search_fields = ("product_name", "order__number")
    readonly_fields = ("quote_item",)


@admin.register(ProposalConfig)
class ProposalConfigAdmin(AdminOnly, admin.ModelAdmin):
    """Singleton admin: one record holds the cover and about-page background images."""
    def has_add_permission(self, request):
        # Allow creation only if no record exists yet
        return not ProposalConfig.objects.exists()

    fieldsets = (
        ("Imagens de Fundo da Proposta", {
            "description": "Faça upload das imagens decorativas usadas no PDF da proposta ao cliente. "
                           "Tamanho recomendado: A4 portrait (2480 × 3508 px).",
            "fields": ("cover_image", "about_image"),
        }),
    )
