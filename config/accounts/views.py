from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
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


@require_http_methods(["POST"])
def change_password(request):
    """Allows a user to change their own password by confirming current password."""
    username = (request.POST.get('username') or '').strip()
    old_password = request.POST.get('old_password') or ''
    new_password1 = request.POST.get('new_password1') or ''
    new_password2 = request.POST.get('new_password2') or ''

    if not old_password or not new_password1 or not new_password2:
        messages.error(request, 'Preencha todos os campos de senha.')
        return redirect('accounts:login')

    if new_password1 != new_password2:
        messages.error(request, 'A nova senha e a confirmação não conferem.')
        return redirect('accounts:login')

    if old_password == new_password1:
        messages.error(request, 'A nova senha deve ser diferente da senha atual.')
        return redirect('accounts:login')

    # Logged-in flow: validates against current user.
    if request.user.is_authenticated:
        user = request.user
        if not user.check_password(old_password):
            messages.error(request, 'Senha atual inválida.')
            return redirect('accounts:login')

        user.set_password(new_password1)
        user.save(update_fields=['password'])
        update_session_auth_hash(request, user)
        messages.success(request, 'Senha alterada com sucesso.')
        return redirect('accounts:login')

    # Login-screen flow: validate identity using username + old password.
    if not username:
        messages.error(request, 'Informe seu usuário para alterar a senha.')
        return redirect('accounts:login')

    user = authenticate(request, username=username, password=old_password)
    if user is None:
        messages.error(request, 'Usuário ou senha atual inválidos.')
        return redirect('accounts:login')

    user.set_password(new_password1)
    user.save(update_fields=['password'])
    messages.success(request, 'Senha alterada com sucesso. Faça login com a nova senha.')
    return redirect('accounts:login')
