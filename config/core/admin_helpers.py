"""
Shared permission helpers for all admin.py files.
"""


def _is_admin(user):
    """True for staff, ADMIN, OWNER or Django superuser."""
    return (
        user.is_superuser
        or user.is_staff
        or getattr(user, "role", None) in ("ADMIN", "OWNER")
    )


class AdminOnly:
    """Somente admins/donos podem visualizar ou modificar este modelo."""
    def has_view_permission(self, request, obj=None):   return _is_admin(request.user)
    def has_add_permission(self, request):              return _is_admin(request.user)
    def has_change_permission(self, request, obj=None): return _is_admin(request.user)
    def has_delete_permission(self, request, obj=None): return _is_admin(request.user)


class SellerAccess:
    """Vendedores podem visualizar/adicionar/editar/excluir (must be authenticated)."""
    def has_view_permission(self, request, obj=None):   return request.user.is_authenticated
    def has_add_permission(self, request):              return request.user.is_authenticated
    def has_change_permission(self, request, obj=None): return request.user.is_authenticated
    def has_delete_permission(self, request, obj=None): return request.user.is_authenticated
