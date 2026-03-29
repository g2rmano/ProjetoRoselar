from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib import messages
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.conf import settings


def csrf_failure_view(request, reason=""):
    """
    Custom CSRF failure handler: delete the stale csrftoken cookie
    and redirect back so the page loads fresh with a valid token.
    """
    response = redirect(request.path or reverse('accounts:login'))
    response.delete_cookie(
        settings.CSRF_COOKIE_NAME,
        path=settings.CSRF_COOKIE_PATH,
        domain=settings.CSRF_COOKIE_DOMAIN,
    )
    messages.warning(request, 'Sessão expirada. Por favor, tente novamente.')
    return response


@ensure_csrf_cookie
def login(request):
    # Redirect if already logged in
    if request.user.is_authenticated:
        return redirect(reverse('core:index'))
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            remember = request.POST.get('remember')
            if not remember:
                # Session expires when the browser closes
                request.session.set_expiry(0)
            else:
                # Session lasts 30 days
                request.session.set_expiry(60 * 60 * 24 * 30)
            auth_login(request, user)
            messages.success(request, f'Bem-vindo de volta, {user.username}!')
            next_url = request.GET.get('next', '')
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                next_url = reverse('core:index')
            return redirect(next_url)
        else:
            messages.error(request, 'Nome de usuário ou senha inválidos.')
            return render(request, 'accounts/login.html', {'login_failed': True, 'username_value': username})
    
    return render(request, 'accounts/login.html')

def logout(request):
    auth_logout(request)
    messages.info(request, 'Você saiu da sua conta com sucesso.')
    return redirect('accounts:login')
