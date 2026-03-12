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
        ("Dados de Acesso", {"fields": ("username", "password")}),
        ("Informações Pessoais", {"fields": ("first_name", "last_name", "email")}),
        ("Perfil", {"fields": ("role", "individual_target_value", "phone")}),
        ("Permissões", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Datas Importantes", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        ("Novo Usuário", {
            "classes": ("wide",),
            "fields": ("username", "email", "role", "password1", "password2"),
        }),
    )
    list_display = ("username", "email", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")
