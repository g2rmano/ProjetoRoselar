from django.contrib import admin
from .models import Quote, QuoteItem, Order, OrderItem


class QuoteItemInline(admin.TabularInline):
    model = QuoteItem
    extra = 1
    fields = ("supplier", "product_name", "quantity", "unit_value", "condition_text")


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("number", "customer", "seller", "status", "quote_date", "delivery_deadline", "created_at")
    list_filter = ("status", "quote_date", "freight_responsible", "created_at")
    search_fields = ("number", "customer__name", "seller__username")
    inlines = [QuoteItemInline]
    fieldsets = (
        ("Informações Básicas", {
            "fields": ("number", "customer", "seller", "status", "quote_date")
        }),
        ("Prazo de Entrega", {
            "fields": ("delivery_deadline",)
        }),
        ("Frete", {
            "fields": ("freight_value", "freight_responsible", "shipping_company", "shipping_payment_method")
        }),
        ("Desconto", {
            "fields": ("discount_percent", "discount_authorized_by", "discount_authorized_at")
        }),
        ("Pagamento", {
            "fields": ("payment_description",)
        }),
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("number", "quote", "supplier", "status", "is_total_conference", "created_at")
    list_filter = ("status", "is_total_conference", "created_at")
    search_fields = ("number", "quote__number")


@admin.register(QuoteItem)
class QuoteItemAdmin(admin.ModelAdmin):
    list_display = ("id", "quote", "product_name", "supplier", "quantity", "unit_value")
    list_filter = ("quote__status",)
    search_fields = ("product_name", "quote__number")


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "product_name", "quantity", "purchase_unit_cost")
    search_fields = ("product_name", "order__number")
