from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    path("quotes/new/", views.quote_create, name="quote_create"),
    path("quotes/<int:quote_id>/", views.quote_detail, name="quote_detail"),
    path("quotes/<int:quote_id>/edit/", views.quote_edit, name="quote_edit"),
    path("quotes/<int:quote_id>/convert/", views.quote_convert_to_orders, name="quote_convert"),
]
