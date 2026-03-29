from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import path, re_path, include
from django.views.static import serve


@login_required
def protected_media(request, path):
    """Serve media files only to authenticated users."""
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("sales/", include("sales.urls")),
    path("calendario/", include("calendar_app.urls")),
]

# Serve media files — always require authentication
if settings.DEBUG:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", protected_media),
    ]
else:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", protected_media),
    ]
