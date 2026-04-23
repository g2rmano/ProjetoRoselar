from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import User
from core.admin_helpers import AdminOnly

# ── Título do painel admin ───────────────────────────────────────────────
admin.site.site_header = "Roselar — Administração"
admin.site.site_title = "Roselar Admin"
admin.site.index_title = "Painel de Controle"


@admin.register(User)
class UserAdmin(AdminOnly, DjangoUserAdmin):
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Informações", {"fields": ("first_name", "last_name", "email", "phone")}),
        ("Perfil", {"fields": ("role", "individual_target_value", "is_active")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "role", "password1", "password2"),
        }),
    )
    list_display = ("username", "first_name", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("username", "first_name", "last_name")
    actions = ["delete_selected"]

    def save_model(self, request, obj, form, change):
        # Auto-set is_staff so users can access admin panel
        obj.is_staff = True
        obj.is_superuser = (obj.role == "ADMIN")
        super().save_model(request, obj, form, change)
