from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib import messages
from django.urls import reverse

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
            next_url = request.GET.get('next') or reverse('core:index')
            return redirect(next_url)
        else:
            messages.error(request, 'Nome de usuário ou senha inválidos.')
            return render(request, 'accounts/login.html', {'login_failed': True, 'username_value': username})
    
    return render(request, 'accounts/login.html')

def logout(request):
    auth_logout(request)
    messages.info(request, 'Você saiu da sua conta com sucesso.')
    return redirect('core:index')
