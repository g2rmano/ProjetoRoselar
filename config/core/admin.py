from django.contrib import admin
from .models import Customer, Supplier, SupplierPaymentOption, ShippingCompany, PaymentTariff, ArchitectCommission


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "email", "created_at")
    search_fields = ("name", "phone", "email", "document")
    list_filter = ("created_at",)


class SupplierPaymentOptionInline(admin.TabularInline):
    model = SupplierPaymentOption
    extra = 1
    fields = ("description", "days_to_pay", "is_default")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "supplier_number", "email", "phone", "created_at")
    search_fields = ("name", "supplier_number", "email")
    list_filter = ("created_at",)
    inlines = [SupplierPaymentOptionInline]


@admin.register(ShippingCompany)
class ShippingCompanyAdmin(admin.ModelAdmin):
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
class PaymentTariffAdmin(admin.ModelAdmin):
    list_display = ("payment_type", "installments", "fee_percent", "get_description")
    list_filter = ("payment_type",)
    list_editable = ("fee_percent",)
    ordering = ("payment_type", "installments")
    
    fieldsets = (
        ("Configuração da Tarifa", {
            "fields": ("payment_type", "installments", "fee_percent"),
            "description": "Configure a porcentagem de acréscimo para cada método de pagamento e número de parcelas."
        }),
    )
    
    def get_description(self, obj):
        if obj.installments == 1:
            return "À vista"
        return f"{obj.installments}x"
    get_description.short_description = "Descrição"


@admin.register(ArchitectCommission)
class ArchitectCommissionAdmin(admin.ModelAdmin):
    list_display = ("commission_percent", "updated_at")
    
    fieldsets = (
        ("Configuração da Comissão", {
            "fields": ("commission_percent",),
            "description": "Configure a porcentagem fixa de comissão do arquiteto aplicada a todos os itens quando o cliente possui arquiteto."
        }),
    )
    
    def has_add_permission(self, request):
        # Singleton - não permite adicionar mais de um registro
        return not ArchitectCommission.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        # Não permite deletar o registro único
        return False
