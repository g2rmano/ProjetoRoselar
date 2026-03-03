from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import User

# ── Título do painel admin ───────────────────────────────────────────────
admin.site.site_header = "Roselar — Administração"
admin.site.site_title = "Roselar Admin"
admin.site.index_title = "Painel de Controle"


# ── Permission helpers ────────────────────────────────────────────────
def _is_admin(user):
    """True for ADMIN, OWNER or Django superuser."""
    return user.is_superuser or getattr(user, "role", None) in ("ADMIN", "OWNER")


class AdminOnly:
    """Restrict this model to admins/owners only."""
    def has_view_permission(self, request, obj=None):   return _is_admin(request.user)
    def has_add_permission(self, request):              return _is_admin(request.user)
    def has_change_permission(self, request, obj=None): return _is_admin(request.user)
    def has_delete_permission(self, request, obj=None): return _is_admin(request.user)


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
