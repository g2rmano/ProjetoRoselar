from django.urls import path
from .views import home, login, logout, dashboard

app_name = "core"

urlpatterns = [
    path("", home, name="index"),
    path("login/", login, name="login"),
    path("logout/", logout, name="logout"),
    path("dashboard/", dashboard, name="dashboard"),
]
