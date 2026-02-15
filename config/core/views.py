from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .models import Customer, ShippingCompany
import json

def home(request):
    return render(request, "core/index.html")

@login_required
def dashboard(request):
    return render(request, 'core/dashboard.html')

@login_required
def search_customer(request):
    """Search for customer by CPF or CNPJ"""
    document = request.GET.get('document', '').strip()
    
    if not document:
        return JsonResponse({'found': False})
    
    # Try to find customer by CPF or CNPJ
    customer = Customer.objects.filter(cpf=document).first() or Customer.objects.filter(cnpj=document).first()
    
    if customer:
        return JsonResponse({
            'found': True,
            'id': customer.id,
            'name': customer.name,
            'cpf': customer.cpf,
            'cnpj': customer.cnpj,
            'phone': customer.phone,
            'email': customer.email,
        })
    
    return JsonResponse({'found': False})

@login_required
@require_http_methods(["POST"])
def create_customer(request):
    """Create a new customer"""
    try:
        data = json.loads(request.body)
        
        customer = Customer.objects.create(
            name=data.get('name'),
            cpf=data.get('cpf', ''),
            cnpj=data.get('cnpj', ''),
            phone=data.get('phone', ''),
            email=data.get('email', ''),
        )
        
        return JsonResponse({
            'success': True,
            'customer': {
                'id': customer.id,
                'name': str(customer),
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)

@login_required
def search_customer_by_name(request):
    """Search for customers by name with fuzzy matching"""
    query = request.GET.get('query', '').strip()
    
    if not query or len(query) < 2:
        return JsonResponse({'results': []})
    
    # Use icontains for basic fuzzy search
    customers = Customer.objects.filter(name__icontains=query)[:3]
    
    results = []
    for customer in customers:
        results.append({
            'id': customer.id,
            'name': customer.name,
            'display': str(customer),  # Shows "Name (PF/PJ - document)"
            'cpf': customer.cpf or '',
            'cnpj': customer.cnpj or '',
        })
    
    return JsonResponse({'results': results})

@login_required
def get_shipping_company_payment_methods(request, company_id):
    """Get payment methods for a specific shipping company"""
    try:
        company = ShippingCompany.objects.get(id=company_id, is_active=True)
        return JsonResponse({
            'success': True,
            'payment_methods': company.payment_methods or ''
        })
    except ShippingCompany.DoesNotExist:
        return JsonResponse({
            'success': False,
            'payment_methods': ''
        }, status=404)
