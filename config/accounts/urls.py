from django.urls import path
from .views import login, logout, change_password

app_name = "accounts"

urlpatterns = [
    path("login/", login, name="login"),
    path("change-password/", change_password, name="change_password"),
    path("logout/", logout, name="logout"),
]
