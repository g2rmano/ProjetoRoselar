import logging

from django.db import OperationalError, ProgrammingError
from django.shortcuts import redirect
from django.urls import reverse

logger = logging.getLogger(__name__)

# URL prefixes that are always public (no login required)
PUBLIC_PREFIXES = [
    '/accounts/login/',
    '/accounts/logout/',
    '/admin/',
    '/static/',
    '/health/',
]


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info

        # Always allow public paths through without touching the DB
        if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return self.get_response(request)

        try:
            is_authenticated = request.user.is_authenticated
        except (OperationalError, ProgrammingError) as exc:
            # DB is unavailable — fail open so the app can still serve
            # requests (e.g. static assets, cached pages) and Railway's
            # healthcheck continues to pass.
            logger.warning(
                "LoginRequiredMiddleware: DB unavailable, allowing request "
                "through for path %s (%s: %s)",
                path, type(exc).__name__, exc,
            )
            return self.get_response(request)

        if not is_authenticated:
            login_url = reverse('accounts:login')
            return redirect(f'{login_url}?next={path}')

        return self.get_response(request)
