from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required

def home(request):
    return render(request, "core/index.html")

def login(request):
    # Redirect if already logged in
    if request.user.is_authenticated:
        return redirect('core:index')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            auth_login(request, user)
            messages.success(request, f'Bem-vindo de volta, {user.username}!')
            # Redirect to next page if specified, otherwise to home
            next_url = request.GET.get('next', 'core:index')
            return redirect(next_url)
        else:
            messages.error(request, 'Nome de usuário ou senha inválidos.')
    
    return render(request, 'core/login.html')

def logout(request):
    auth_logout(request)
    messages.info(request, 'Você saiu da sua conta com sucesso.')
    return redirect('core:index')

@login_required
def dashboard(request):
    return render(request, 'core/dashboard.html')
