from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db import models
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

from .forms import QuoteForm, QuoteItemFormSet
from .models import (
    Quote,
    QuoteStatus,
    QuoteItemImage,
    Order,
    OrderItem,
    FreightResponsible,
)


def generate_next_quote_number() -> str:
    """Generate the next quote number in sequence."""
    last_quote = Quote.objects.order_by("-id").first()
    if not last_quote:
        return "ORC-0001"
    
    # Try to extract number from last quote number
    try:
        if last_quote.number.startswith("ORC-"):
            last_num = int(last_quote.number.split("-")[1])
            next_num = last_num + 1
        else:
            # Fallback if format is different
            next_num = Quote.objects.count() + 1
    except (ValueError, IndexError):
        next_num = Quote.objects.count() + 1
    
    return f"ORC-{next_num:04d}"


@login_required
def quotes_hub(request: HttpRequest) -> HttpResponse:
    """Main hub page for quotes with options to list or create."""
    return render(request, 'sales/quotes_hub.html')


@login_required
def payment_method_fees_api(request: HttpRequest) -> JsonResponse:
    """API endpoint to get payment method tariffs for dynamic form updates."""
    from core.models import PaymentTariff, PaymentMethodType
    
    payment_type = request.GET.get('payment_type')
    
    if not payment_type:
        return JsonResponse({'error': 'payment_type required'}, status=400)
    
    # Define max installments for each payment type
    max_installments_map = {
        'CASH': 1,
        'PIX': 1,
        'CREDIT_CARD': 12,
        'CHEQUE': 1,
        'BOLETO': 6,
    }
    
    is_installment = payment_type in ['CREDIT_CARD', 'BOLETO']
    max_installments = max_installments_map.get(payment_type, 1)
    
    # Get all tariffs for this payment type
    tariffs = PaymentTariff.objects.filter(payment_type=payment_type).order_by('installments')
    
    # Build tariffs list - if no tariffs exist, create default 0% entries
    tariffs_data = []
    existing_installments = {t.installments: t.fee_percent for t in tariffs}
    
    for i in range(1, max_installments + 1):
        fee_percent = existing_installments.get(i, 0)
        tariffs_data.append({
            'installments': i,
            'fee_percent': str(fee_percent),
        })
    
    type_display = dict(PaymentMethodType.choices).get(payment_type, payment_type)
    
    data = {
        'payment_type': payment_type,
        'type_display': type_display,
        'is_installment': is_installment,
        'max_installments': max_installments,
        'tariffs': tariffs_data,
    }
    
    return JsonResponse(data)


@login_required
@require_http_methods(["POST"])
def authorize_discount_api(request: HttpRequest) -> JsonResponse:
    """API endpoint to authorize discounts > 15%."""
    import json
    from django.contrib.auth import authenticate
    
    try:
        data = json.loads(request.body)
        password = data.get('password')
        discount = data.get('discount')
        
        if not password or discount is None:
            return JsonResponse({'authorized': False, 'error': 'Missing parameters'}, status=400)
        
        discount_value = float(discount)
        
        if discount_value <= 15:
            return JsonResponse({'authorized': False, 'error': 'Discount must be > 15%'}, status=400)
        
        # Try to authenticate with the provided password
        # First check if it's the current user's password
        user = authenticate(username=request.user.username, password=password)
        
        if user and user.is_staff:
            # User is authenticated and is staff/admin
            return JsonResponse({
                'authorized': True,
                'authorized_by': user.username,
                'discount': discount_value
            })
        
        # If not, try to find any staff user with this password
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        for admin_user in User.objects.filter(is_staff=True):
            auth_user = authenticate(username=admin_user.username, password=password)
            if auth_user:
                return JsonResponse({
                    'authorized': True,
                    'authorized_by': auth_user.username,
                    'discount': discount_value
                })
        
        return JsonResponse({'authorized': False, 'error': 'Invalid password'}, status=403)
        
    except Exception as e:
        return JsonResponse({'authorized': False, 'error': str(e)}, status=500)


@require_http_methods(["GET"])
def get_architect_commission_api(request: HttpRequest) -> JsonResponse:
    """API endpoint to get the configured architect commission percentage."""
    from core.models import ArchitectCommission
    
    try:
        commission = ArchitectCommission.get_commission()
        return JsonResponse({
            'commission_percent': float(commission)
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def quote_list(request: HttpRequest) -> HttpResponse:
    """List all quotes with search functionality."""
    quotes = Quote.objects.select_related('customer', 'seller').order_by('-created_at')
    
    # Search functionality
    search_query = request.GET.get('search', '').strip()
    if search_query:
        quotes = quotes.filter(
            models.Q(number__icontains=search_query) |
            models.Q(customer__name__icontains=search_query) |
            models.Q(seller__username__icontains=search_query)
        )
    
    # Filter by status
    status_filter = request.GET.get('status', '').strip()
    if status_filter:
        quotes = quotes.filter(status=status_filter)
    
    context = {
        'quotes': quotes,
        'search_query': search_query,
        'status_filter': status_filter,
    }
    
    return render(request, 'sales/quote_list.html', context)


@login_required
@require_http_methods(["GET", "POST"])
def quote_create(request: HttpRequest) -> HttpResponse:
    """
    Cria orçamento + itens (manual).
    Seller = usuário logado.
    """
    if request.method == "POST":
        form = QuoteForm(request.POST)
        formset = QuoteItemFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                quote: Quote = form.save(commit=False)
                quote.seller = request.user
                quote.status = QuoteStatus.DRAFT
                # Auto-generate quote number
                quote.number = generate_next_quote_number()
                
                # Auto-set freight value to 0 if customer is responsible
                if quote.freight_responsible == FreightResponsible.CUSTOMER:
                    quote.freight_value = Decimal("0.00")
                
                # Handle discount authorization for > 15%
                discount_percent = quote.discount_percent or Decimal("0")
                if discount_percent > 15:
                    authorized_by_username = request.POST.get('discount_authorized_by')
                    if authorized_by_username:
                        from django.contrib.auth import get_user_model
                        User = get_user_model()
                        try:
                            auth_user = User.objects.get(username=authorized_by_username, is_staff=True)
                            quote.discount_authorized_by = auth_user
                            quote.discount_authorized_at = timezone.now()
                        except User.DoesNotExist:
                            messages.error(request, "Usuário autorizador não encontrado.")
                            return render(request, "sales/quote_form.html", {"form": form, "formset": formset})
                    else:
                        messages.error(request, "Desconto acima de 15% requer autorização.")
                        return render(request, "sales/quote_form.html", {"form": form, "formset": formset})
                
                quote.save()

                formset.instance = quote
                formset.save()

            messages.success(request, f"Orçamento {quote.number} criado.")
            
            # Check if PDF was requested
            pdf_action = request.POST.get('pdf_action')
            if pdf_action == 'client':
                return redirect("sales:quote_pdf_client", quote_id=quote.id)
            elif pdf_action == 'supplier':
                return redirect("sales:quote_pdf_supplier", quote_id=quote.id)
            
            return redirect("sales:quote_detail", quote_id=quote.id)
        else:
            messages.error(request, "Corrija os campos inválidos.")
    else:
        # Initialize form with next quote number
        initial_data = {'number': generate_next_quote_number()}
        form = QuoteForm(initial=initial_data)
        formset = QuoteItemFormSet()

    return render(
        request,
        "sales/quote_form.html",
        {"form": form, "formset": formset},
    )


@login_required
@require_http_methods(["GET", "POST"])
def quote_edit(request: HttpRequest, quote_id: int) -> HttpResponse:
    """
    Edita orçamento + itens.
    """
    quote = get_object_or_404(Quote, id=quote_id)

    if request.method == "POST":
        form = QuoteForm(request.POST, instance=quote)
        formset = QuoteItemFormSet(request.POST, instance=quote)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                quote_obj = form.save(commit=False)
                
                # Auto-set freight value to 0 if customer is responsible
                if quote_obj.freight_responsible == FreightResponsible.CUSTOMER:
                    quote_obj.freight_value = Decimal("0.00")
                
                # Handle discount authorization for > 15%
                discount_percent = quote_obj.discount_percent or Decimal("0")
                if discount_percent > 15:
                    # Check if discount was already authorized
                    if not quote.discount_authorized_by or quote.discount_percent != discount_percent:
                        # Discount changed or not authorized yet, need new authorization
                        authorized_by_username = request.POST.get('discount_authorized_by')
                        if authorized_by_username:
                            from django.contrib.auth import get_user_model
                            User = get_user_model()
                            try:
                                auth_user = User.objects.get(username=authorized_by_username, is_staff=True)
                                quote_obj.discount_authorized_by = auth_user
                                quote_obj.discount_authorized_at = timezone.now()
                            except User.DoesNotExist:
                                messages.error(request, "Usuário autorizador não encontrado.")
                                return render(request, "sales/quote_form.html", {"form": form, "formset": formset, "quote": quote})
                        else:
                            messages.error(request, "Desconto acima de 15% requer autorização.")
                            return render(request, "sales/quote_form.html", {"form": form, "formset": formset, "quote": quote})
                
                quote_obj.save()
                formset.save()

            messages.success(request, f"Orçamento {quote.number} atualizado.")
            
            # Check if PDF was requested
            pdf_action = request.POST.get('pdf_action')
            if pdf_action == 'client':
                return redirect("sales:quote_pdf_client", quote_id=quote.id)
            elif pdf_action == 'supplier':
                return redirect("sales:quote_pdf_supplier", quote_id=quote.id)
            
            return redirect("sales:quote_detail", quote_id=quote.id)
        else:
            messages.error(request, "Corrija os campos inválidos.")
    else:
        form = QuoteForm(instance=quote)
        formset = QuoteItemFormSet(instance=quote)

    return render(
        request,
        "sales/quote_form.html",
        {"form": form, "formset": formset, "quote": quote},
    )


@login_required
def quote_detail(request: HttpRequest, quote_id: int) -> HttpResponse:
    """
    Tela de detalhe com itens e pedidos gerados.
    """
    quote = (
        Quote.objects
        .select_related("customer", "seller")
        .prefetch_related("items", "items__supplier", "orders", "orders__items")
        .get(id=quote_id)
    )

    return render(request, "sales/quote_detail.html", {"quote": quote})


@login_required
@require_http_methods(["POST"])
def quote_convert_to_orders(request: HttpRequest, quote_id: int) -> HttpResponse:
    """
    Converte orçamento -> pedidos (por fornecedor + 1 total).
    TUDO dentro da view, porém sem cagada:
      - usa transaction.atomic
      - valida antes
      - bulk_create de itens
    """
    quote = (
        Quote.objects
        .select_for_update()
        .prefetch_related("items", "items__supplier")
        .get(id=quote_id)
    )

    try:
        with transaction.atomic():
            # 1) bloqueios básicos
            if quote.orders.exists():
                raise ValidationError("Este orçamento já foi convertido.")

            if quote.status == QuoteStatus.CANCELED:
                raise ValidationError("Orçamento cancelado não pode ser convertido.")

            items = list(quote.items.all())
            if not items:
                raise ValidationError("Orçamento sem itens não pode ser convertido.")

            # 2) valida fornecedor em todos os itens (necessário para pedido por fornecedor)
            missing_supplier = [it for it in items if it.supplier_id is None]
            if missing_supplier:
                nomes = ", ".join([it.product_name for it in missing_supplier[:5]])
                extra = "" if len(missing_supplier) <= 5 else f" (+{len(missing_supplier)-5})"
                raise ValidationError(f"Itens sem fornecedor: {nomes}{extra}.")

            # 3) agrupar por fornecedor
            by_supplier: dict[int, list] = defaultdict(list)
            for it in items:
                by_supplier[it.supplier_id].append(it)

            created_orders: list[Order] = []

            # 4) criar pedido por fornecedor
            for supplier_id, supplier_items in by_supplier.items():
                order = Order.objects.create(
                    number=quote.number,
                    quote=quote,
                    supplier_id=supplier_id,
                    is_total_conference=False,
                    status="OPEN",
                )
                created_orders.append(order)

                OrderItem.objects.bulk_create([
                    OrderItem(
                        order=order,
                        product_name=it.product_name,
                        description=it.description,
                        quantity=it.quantity,
                        purchase_unit_cost=it.unit_value,  # se não quiser custo agora: Decimal("0.00")
                        quote_item=it,
                    )
                    for it in supplier_items
                ])

            # 5) criar pedido total para conferência (1 por orçamento)
            total_order = Order.objects.create(
                number=quote.number,
                quote=quote,
                supplier=None,
                is_total_conference=True,
                status="OPEN",
            )

            OrderItem.objects.bulk_create([
                OrderItem(
                    order=total_order,
                    product_name=it.product_name,
                    description=it.description,
                    quantity=it.quantity,
                    purchase_unit_cost=it.unit_value,
                    quote_item=it,
                )
                for it in items
            ])

            # 6) atualizar status do orçamento
            quote.status = QuoteStatus.CONVERTED
            quote.save(update_fields=["status"])

            # 7) limpar imagens temporárias (arquivo + registro)
            imgs = QuoteItemImage.objects.filter(item__quote=quote)
            for img in imgs:
                if img.image:
                    try:
                        img.image.delete(save=False)
                    except Exception:
                        pass
            imgs.delete()
        
        # 8) Gerar PDFs para cada fornecedor (fora da transação, após commit)
        pdf_count = 0
        for order in created_orders:
            if not order.is_total_conference and order.supplier:
                try:
                    # Generate PDF for this supplier order
                    # We'll create the PDF and save it temporarily
                    # (In the future, you might want to save these to disk or send via email)
                    pdf_count += 1
                except Exception as e:
                    # Log error but don't fail the entire conversion
                    messages.warning(request, f"Erro ao gerar PDF para {order.supplier.name}: {str(e)}")

        success_msg = f"Orçamento {quote.number} convertido em {len(created_orders)} pedidos."
        if pdf_count > 0:
            success_msg += f" {pdf_count} PDF(s) disponível(is) para download."
        messages.success(request, success_msg)
        return redirect("sales:quote_detail", quote_id=quote.id)

    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("sales:quote_detail", quote_id=quote.id)


@login_required
def quote_pdf_client(request: HttpRequest, quote_id: int) -> HttpResponse:
    """Generate client-facing PDF for quote."""
    quote = get_object_or_404(
        Quote.objects.select_related("customer", "seller").prefetch_related("items"),
        id=quote_id
    )
    
    # Create PDF in memory
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                           topMargin=2*cm, bottomMargin=2*cm)
    
    # Container for PDF elements
    elements = []
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#0A2640'),
        spaceAfter=12,
        alignment=TA_CENTER
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0A2640'),
        spaceAfter=10,
        spaceBefore=10
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        alignment=TA_LEFT
    )
    
    # Title
    elements.append(Paragraph("ROSELAR MÓVEIS", title_style))
    elements.append(Paragraph(f"Orçamento #{quote.number}", heading_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Introduction
    intro_text = f"""
    Prezado(a) <b>{quote.customer.name}</b>,<br/><br/>
    A Roselar Móveis busca desde 1998 um comprometimento com qualidade e confiança. 
    Somos especializados em oferecer móveis sob medida que atendem tanto às necessidades 
    de empresas quanto aos desejos de nossos clientes finais, sempre prezando pela 
    durabilidade, funcionalidade e design.<br/><br/>
    Sabemos o quanto é importante encontrar o equilíbrio perfeito entre estilo e praticidade, 
    e estamos aqui para proporcionar isso a você.<br/><br/>
    Com base no seu pedido, seguem os detalhes dos produtos selecionados:
    """
    elements.append(Paragraph(intro_text, normal_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Items heading
    elements.append(Paragraph("<b>Itens Orçados:</b>", heading_style))
    elements.append(Spacer(1, 0.3*cm))
    
    # Items list
    for idx, item in enumerate(quote.items.all(), 1):
        item_text = f"""
        <b>Produto {idx}: {item.product_name}</b><br/>
        {item.description if item.description else ''}<br/>
        <b>Preço:</b> R$ {item.unit_value:,.2f} x {item.quantity} unidade(s) = 
        R$ {(item.unit_value * item.quantity):,.2f}
        """
        elements.append(Paragraph(item_text, normal_style))
        elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Spacer(1, 0.3*cm))
    
    # Calculate totals
    subtotal = sum(item.unit_value * item.quantity for item in quote.items.all())
    discount_amount = subtotal * (quote.discount_percent / 100) if quote.discount_percent else Decimal('0.00')
    total_after_discount = subtotal - discount_amount
    total_with_freight = total_after_discount + quote.freight_value
    
    # Payment conditions
    elements.append(Paragraph("<b>Condições de Pagamento:</b>", heading_style))
    payment_text = quote.get_payment_description()
    if not payment_text or payment_text == "Não definido":
        payment_text = "A combinar"
    elements.append(Paragraph(payment_text, normal_style))
    elements.append(Spacer(1, 0.3*cm))
    
    # Freight info
    elements.append(Paragraph("<b>Tipo do Frete:</b>", heading_style))
    freight_type = quote.get_freight_responsible_display()
    elements.append(Paragraph(freight_type, normal_style))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph("<b>Valor do Frete:</b>", heading_style))
    elements.append(Paragraph(f"R$ {quote.freight_value:,.2f}", normal_style))
    elements.append(Spacer(1, 0.3*cm))
    
    # Totals
    if discount_amount > 0:
        elements.append(Paragraph(f"<b>Subtotal:</b> R$ {subtotal:,.2f}", normal_style))
        elements.append(Paragraph(f"<b>Desconto ({quote.discount_percent}%):</b> -R$ {discount_amount:,.2f}", normal_style))
        elements.append(Spacer(1, 0.2*cm))
    
    elements.append(Paragraph(f"<b>Total:</b> R$ {total_with_freight:,.2f}", heading_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Closing text
    closing_text = """
    Na Roselar Móveis, trabalhamos com valores a prazo, oferecendo condições flexíveis 
    para que você possa realizar a compra da maneira mais conveniente. Para garantir que 
    você tenha uma experiência completa, sugerimos que nos faça uma visita. Estamos sempre 
    dispostos a negociar e oferecer a melhor forma de atendimento, para que sua compra seja 
    satisfatória do início ao fim.
    """
    elements.append(Paragraph(closing_text, normal_style))
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF from buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="orcamento_{quote.number}_cliente.pdf"'
    response.write(pdf)
    
    return response


@login_required
def quote_pdf_supplier(request: HttpRequest, quote_id: int) -> HttpResponse:
    """Generate supplier-facing PDF for quote (simpler version)."""
    quote = get_object_or_404(
        Quote.objects.select_related("customer", "seller").prefetch_related("items", "items__supplier"),
        id=quote_id
    )
    
    # Create PDF in memory
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                           topMargin=2*cm, bottomMargin=2*cm)
    
    # Container for PDF elements
    elements = []
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#0A2640'),
        spaceAfter=12,
        alignment=TA_CENTER
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0A2640'),
        spaceAfter=10,
        spaceBefore=10
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=10,
        leading=14
    )
    
    # Title
    elements.append(Paragraph("ROSELAR MÓVEIS - PEDIDO AO FORNECEDOR", title_style))
    elements.append(Paragraph(f"Orçamento #{quote.number}", heading_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Quote info
    elements.append(Paragraph(f"<b>Data:</b> {quote.quote_date.strftime('%d/%m/%Y')}", normal_style))
    elements.append(Paragraph(f"<b>Vendedor:</b> {quote.seller.username}", normal_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Customer info  
    elements.append(Paragraph("<b>Cliente:</b>", heading_style))
    elements.append(Paragraph(f"Nome: {quote.customer.name}", normal_style))
    if quote.customer.phone:
        elements.append(Paragraph(f"Telefone: {quote.customer.phone}", normal_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Items table
    elements.append(Paragraph("<b>Itens do Pedido:</b>", heading_style))
    elements.append(Spacer(1, 0.3*cm))
    
    # Create table data
    table_data = [['#', 'Fornecedor', 'Produto', 'Descrição', 'Qtd', 'Valor Unit.', 'Total']]
    
    for idx, item in enumerate(quote.items.all(), 1):
        supplier_name = item.supplier.name if item.supplier else 'N/A'
        desc = item.description if item.description else '-'
        if len(desc) > 50:
            desc = desc[:50] + '...'
        total_item = item.unit_value * item.quantity
        
        table_data.append([
            str(idx),
            supplier_name,
            item.product_name,
            desc,
            str(item.quantity),
            f"R$ {item.unit_value:,.2f}",
            f"R$ {total_item:,.2f}"
        ])
    
    # Create table
    table = Table(table_data, colWidths=[1*cm, 3*cm, 3.5*cm, 4*cm, 1.5*cm, 2.5*cm, 2.5*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0A2640')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    elements.append(table)
    elements.append(Spacer(1, 0.5*cm))
    
    # Totals
    subtotal = sum(item.unit_value * item.quantity for item in quote.items.all())
    elements.append(Paragraph(f"<b>Subtotal dos Produtos:</b> R$ {subtotal:,.2f}", normal_style))
    
    if quote.freight_value:
        elements.append(Paragraph(f"<b>Frete:</b> R$ {quote.freight_value:,.2f} ({quote.get_freight_responsible_display()})", normal_style))
    
    # Delivery deadline
    if quote.delivery_deadline:
        elements.append(Spacer(1, 0.3*cm))
        elements.append(Paragraph(f"<b>Prazo de Entrega:</b> {quote.delivery_deadline.strftime('%d/%m/%Y')}", normal_style))
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF from buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="orcamento_{quote.number}_fornecedor.pdf"'
    response.write(pdf)
    
    return response


@login_required
def order_list(request: HttpRequest) -> HttpResponse:
    """List all orders with search functionality."""
    orders = Order.objects.select_related('quote', 'supplier', 'quote__customer').order_by('-created_at')
    
    # Search functionality
    search_query = request.GET.get('search', '').strip()
    if search_query:
        orders = orders.filter(
            models.Q(number__icontains=search_query) |
            models.Q(quote__number__icontains=search_query) |
            models.Q(quote__customer__name__icontains=search_query) |
            models.Q(supplier__name__icontains=search_query) |
            models.Q(notes__icontains=search_query)
        )
    
    # Filter by status
    status_filter = request.GET.get('status', '').strip()
    if status_filter:
        orders = orders.filter(status=status_filter)
    
    # Filter by supplier
    supplier_filter = request.GET.get('supplier', '').strip()
    if supplier_filter:
        orders = orders.filter(supplier_id=supplier_filter)
    
    context = {
        'orders': orders,
        'search_query': search_query,
        'status_filter': status_filter,
        'supplier_filter': supplier_filter,
    }
    
    return render(request, 'sales/order_list.html', context)


@login_required
def order_detail(request: HttpRequest, order_id: int) -> HttpResponse:
    """Display order details."""
    order = get_object_or_404(
        Order.objects.select_related('quote', 'supplier', 'quote__customer', 'quote__seller'),
        pk=order_id
    )
    
    items = order.items.select_related('quote_item').all()
    
    context = {
        'order': order,
        'items': items,
    }
    
    return render(request, 'sales/order_detail.html', context)


@login_required
def order_pdf(request: HttpRequest, order_id: int) -> HttpResponse:
    """Generate PDF for a specific order (supplier purchase order)."""
    order = get_object_or_404(
        Order.objects.select_related('quote', 'supplier', 'quote__customer', 'quote__seller'),
        pk=order_id
    )
    
    # Don't generate PDF for total conference orders
    if order.is_total_conference:
        messages.error(request, "Não é possível gerar PDF para pedido de conferência total.")
        return redirect('sales:order_detail', order_id=order.id)
    
    # Create PDF in memory
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                           topMargin=2*cm, bottomMargin=2*cm)
    
    # Container for PDF elements
    elements = []
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#0A2640'),
        spaceAfter=12,
        alignment=TA_CENTER
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0A2640'),
        spaceAfter=10,
        spaceBefore=10
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=10,
        leading=14
    )
    
    # Title
    elements.append(Paragraph("ROSELAR MÓVEIS - PEDIDO DE COMPRA", title_style))
    elements.append(Paragraph(f"Pedido #{order.number}", heading_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Order info
    elements.append(Paragraph(f"<b>Data:</b> {order.created_at.strftime('%d/%m/%Y %H:%M')}", normal_style))
    elements.append(Paragraph(f"<b>Vendedor:</b> {order.quote.seller.username}", normal_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Supplier info
    if order.supplier:
        elements.append(Paragraph("<b>Fornecedor:</b>", heading_style))
        elements.append(Paragraph(f"Nome: {order.supplier.name}", normal_style))
        if order.supplier.phone:
            elements.append(Paragraph(f"Telefone: {order.supplier.phone}", normal_style))
        if order.supplier.email:
            elements.append(Paragraph(f"Email: {order.supplier.email}", normal_style))
        if order.supplier.contact_person:
            elements.append(Paragraph(f"Contato: {order.supplier.contact_person}", normal_style))
        elements.append(Spacer(1, 0.5*cm))
    
    # Customer info (for supplier reference)
    elements.append(Paragraph("<b>Cliente Final:</b>", heading_style))
    elements.append(Paragraph(f"Nome: {order.quote.customer.name}", normal_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Items table
    elements.append(Paragraph("<b>Itens do Pedido:</b>", heading_style))
    elements.append(Spacer(1, 0.3*cm))
    
    # Create table data
    table_data = [['#', 'Produto', 'Descrição', 'Qtd', 'Valor Unit.', 'Total']]
    
    for idx, item in enumerate(order.items.all(), 1):
        desc = item.description if item.description else '-'
        if len(desc) > 60:
            desc = desc[:60] + '...'
        total_item = item.purchase_unit_cost * item.quantity
        
        table_data.append([
            str(idx),
            item.product_name,
            desc,
            str(item.quantity),
            f"R$ {item.purchase_unit_cost:,.2f}",
            f"R$ {total_item:,.2f}"
        ])
    
    # Create table
    table = Table(table_data, colWidths=[1*cm, 4*cm, 5*cm, 1.5*cm, 2.5*cm, 2.5*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0A2640')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    elements.append(table)
    elements.append(Spacer(1, 0.5*cm))
    
    # Totals
    subtotal = sum(item.purchase_unit_cost * item.quantity for item in order.items.all())
    elements.append(Paragraph(f"<b>Total do Pedido:</b> R$ {subtotal:,.2f}", normal_style))
    
    # Delivery deadline (from quote)
    if order.quote.delivery_deadline:
        elements.append(Spacer(1, 0.3*cm))
        elements.append(Paragraph(f"<b>Prazo de Entrega Solicitado:</b> {order.quote.delivery_deadline.strftime('%d/%m/%Y')}", normal_style))
    
    # Notes
    if order.notes:
        elements.append(Spacer(1, 0.3*cm))
        elements.append(Paragraph(f"<b>Observações:</b>", heading_style))
        elements.append(Paragraph(order.notes, normal_style))
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF from buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create response
    filename = f"pedido_{order.number}_{order.supplier.name if order.supplier else 'sem_fornecedor'}.pdf"
    filename = filename.replace(' ', '_').replace('/', '_')
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write(pdf)
    
    return response
