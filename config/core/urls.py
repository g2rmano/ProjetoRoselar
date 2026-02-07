from django.urls import path
from .views import home, dashboard, search_customer, create_customer, search_customer_by_name

app_name = "core"

urlpatterns = [
    path("", home, name="index"),
    path("dashboard/", dashboard, name="dashboard"),
    path("api/search-customer/", search_customer, name="search_customer"),
    path("api/create-customer/", create_customer, name="create_customer"),
    path("api/search-customer-by-name/", search_customer_by_name, name="search_customer_by_name"),
]
