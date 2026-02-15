from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    # Quotes Hub
    path("quotes/", views.quotes_hub, name="quotes_hub"),
    path("quotes/list/", views.quote_list, name="quote_list"),
    path("quotes/new/", views.quote_create, name="quote_create"),
    path("quotes/<int:quote_id>/", views.quote_detail, name="quote_detail"),
    path("quotes/<int:quote_id>/edit/", views.quote_edit, name="quote_edit"),
    path("quotes/<int:quote_id>/convert/", views.quote_convert_to_orders, name="quote_convert"),
    path("quotes/<int:quote_id>/pdf/client/", views.quote_pdf_client, name="quote_pdf_client"),
    path("quotes/<int:quote_id>/pdf/supplier/", views.quote_pdf_supplier, name="quote_pdf_supplier"),
    
    # API endpoints
    path("api/payment-method-fees/", views.payment_method_fees_api, name="payment_method_fees_api"),
    path("api/authorize-discount/", views.authorize_discount_api, name="authorize_discount_api"),
    path("api/get-architect-commission/", views.get_architect_commission_api, name="get_architect_commission_api"),
    
    # Order URLs
    path("orders/", views.order_list, name="order_list"),
    path("orders/<int:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<int:order_id>/pdf/", views.order_pdf, name="order_pdf"),
]
