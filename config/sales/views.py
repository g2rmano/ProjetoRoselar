from __future__ import annotations

import json
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
from django.utils import timezone
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
    QuoteItem,
    QuoteItemImage,
    Order,
    OrderItem,
    FreightResponsible,
    ProposalConfig,
)
from calendar_app.models import (
    create_quote_followup_events,
    create_delivery_events_for_quote,
)


def generate_next_quote_number() -> str:
    """Generate the next available quote number, skipping any already in use."""
    last_quote = Quote.objects.order_by("-id").first()
    if not last_quote:
        candidate = 1
    else:
        try:
            if last_quote.number.startswith("ORC-"):
                candidate = int(last_quote.number.split("-")[1]) + 1
            else:
                candidate = Quote.objects.count() + 1
        except (ValueError, IndexError):
            candidate = Quote.objects.count() + 1

    # Increment until we find an unused number
    while Quote.objects.filter(number=f"ORC-{candidate:04d}").exists():
        candidate += 1

    return f"ORC-{candidate:04d}"


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
        formset = QuoteItemFormSet(request.POST, request.FILES)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                quote: Quote = form.save(commit=False)
                quote.seller = request.user
                quote.status = QuoteStatus.DRAFT
                quote.quote_date = timezone.localdate()
                quote.number = generate_next_quote_number()
                
                # Defaults for pricing fields (set later in Step 2)
                if quote.discount_percent is None:
                    quote.discount_percent = Decimal("0.0")
                if quote.payment_installments is None:
                    quote.payment_installments = 1
                if quote.payment_fee_percent is None:
                    quote.payment_fee_percent = Decimal("0.0")
                
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

            # Criar eventos de follow-up no calendário (fora da transação)
            try:
                create_quote_followup_events(quote)
            except Exception:
                pass  # Não impede o fluxo principal

            # Audit log
            from core.models import AuditLog, AuditAction
            AuditLog.log(request.user, AuditAction.CREATE_QUOTE,
                         f"Orçamento {quote.number} criado", obj=quote,
                         ip_address=request.META.get('REMOTE_ADDR'))

            messages.success(request, f"Orçamento {quote.number} criado.")
            
            # Check which button was pressed
            action = request.POST.get('action', 'save')
            if action == 'next_step':
                return redirect("sales:quote_simulate", quote_id=quote.id)
            
            return redirect("sales:quote_detail", quote_id=quote.id)
        else:
            messages.error(request, "Corrija os campos inválidos.")
    else:
        # Initialize form with pricing defaults
        initial_data = {
            'discount_percent': Decimal('0.0'),
            'payment_installments': 1,
            'payment_fee_percent': Decimal('0.0'),
        }
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

            # Atualizar evento de follow-up no calendário
            try:
                pass  # Entrega agendada apenas ao converter em pedido
            except Exception:
                pass  # Não impede o fluxo principal

            # Audit log
            from core.models import AuditLog, AuditAction
            AuditLog.log(request.user, AuditAction.EDIT_QUOTE,
                         f"Orçamento {quote.number} editado", obj=quote,
                         ip_address=request.META.get('REMOTE_ADDR'))

            messages.success(request, f"Orçamento {quote.number} atualizado.")
            
            # Check which button was pressed
            action = request.POST.get('action', 'save')
            if action == 'next_step':
                return redirect("sales:quote_simulate", quote_id=quote.id)
            
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

    return render(request, "sales/quote_detail.html", {"quote": quote, "today": timezone.localdate()})


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

    # Parse the required real delivery date from the modal form
    from datetime import date as date_type
    delivery_deadline_str = request.POST.get("delivery_deadline", "").strip()
    try:
        real_deadline = date_type.fromisoformat(delivery_deadline_str) if delivery_deadline_str else None
    except ValueError:
        real_deadline = None

    if not real_deadline:
        messages.error(request, "Data real de entrega é obrigatória para converter o orçamento.")
        return redirect("sales:quote_detail", quote_id=quote_id)

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
                    delivery_deadline=real_deadline,
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
                delivery_deadline=real_deadline,
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
        
        # 8) Criar eventos de entrega no calendário para cada pedido (fora da transação)
        for order in created_orders:
            try:
                create_delivery_events_for_quote(order)
            except Exception:
                pass  # Não impede o fluxo principal

        # 9) Gerar PDFs para cada fornecedor (fora da transação, após commit)
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

        # Audit log + notification
        from core.models import AuditLog, AuditAction, Notification, NotificationType
        AuditLog.log(request.user, AuditAction.CONVERT_ORDER,
                     f"Orçamento {quote.number} convertido em {len(created_orders)} pedidos", obj=quote,
                     ip_address=request.META.get('REMOTE_ADDR'))
        Notification.send(
            quote.seller, f"Pedido confirmado: {quote.number}",
            NotificationType.ORDER_CONFIRMED,
            message=f"Orçamento {quote.number} convertido em pedidos.",
            url=f"/sales/quotes/{quote.id}/",
        )

        messages.success(request, success_msg)
        return redirect("sales:quote_detail", quote_id=quote.id)

    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("sales:quote_detail", quote_id=quote.id)


@login_required
def quote_pdf_client(request: HttpRequest, quote_id: int) -> HttpResponse:
    """
    Client proposal PDF – 3-page Roselar commercial proposal format:

    Page 1 – Cover   (full-bleed background image + "Proposta COMERCIAL")
    Page 2 – Sobre Nós (full-bleed background image + company manifesto)
    Page 3+ – Items  (cream background, header, items with product photos,
                      Proposta Especial footer on last items page)

    Background images are uploaded via Admin → Configuração da Proposta.
    Product images per item come from QuoteItemImage records.
    """
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase.pdfmetrics import stringWidth

    quote = get_object_or_404(
        Quote.objects.select_related("customer", "seller")
                     .prefetch_related("items", "items__images"),
        id=quote_id,
    )

    config = ProposalConfig.get_config()

    buffer = BytesIO()
    page_w, page_h = A4   # 595.28 × 841.89 pt
    c = pdf_canvas.Canvas(buffer, pagesize=A4)

    # ── colour palette ───────────────────────────────────────────
    WHITE = colors.white
    GOLD  = colors.HexColor('#C9A84C')
    NAVY  = colors.HexColor('#0A2640')
    LINEN = colors.HexColor('#FAF7F2')
    GRAY  = colors.HexColor('#888888')
    LGRAY = colors.HexColor('#DDDDDD')

    # ── helper utilities ─────────────────────────────────────────
    def _sw(text, font, size):
        return stringWidth(text, font, size)

    def _spaced_w(text, font, size, cs):
        return _sw(text, font, size) + cs * max(0, len(text) - 1)

    def _draw_spaced(text, x, y, font, size, cs=2.0):
        """Draw text with manual letter-spacing (ReportLab Canvas has no setCharSpace)."""
        c.setFont(font, size)
        cur_x = x
        for ch in text:
            c.drawString(cur_x, y, ch)
            cur_x += _sw(ch, font, size) + cs

    def _draw_spaced_centered(text, cx, y, font, size, cs=2.0):
        tw = _spaced_w(text, font, size, cs)
        _draw_spaced(text, cx - tw / 2, y, font, size, cs)

    def _draw_bg(field, fallback='#1a0f07'):
        drawn = False
        if field:
            try:
                c.drawImage(ImageReader(field.path), 0, 0,
                            width=page_w, height=page_h,
                            preserveAspectRatio=False, mask='auto')
                drawn = True
            except Exception:
                pass
        if not drawn:
            c.setFillColor(colors.HexColor(fallback))
            c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    def _wrap(text, font, size, max_w):
        """Break text into lines that fit max_w."""
        words = text.split()
        lines, cur, cw_acc = [], [], 0
        for word in words:
            ww = _sw(word + ' ', font, size)
            if cur and cw_acc + ww > max_w:
                lines.append(' '.join(cur))
                cur, cw_acc = [word], _sw(word + ' ', font, size)
            else:
                cur.append(word)
                cw_acc += ww
        if cur:
            lines.append(' '.join(cur))
        return lines

    def _fmt_brl(value):
        """Format Decimal/float as Brazilian currency: R$ 1.384,00"""
        s = f"{float(value):,.2f}"
        s = s.replace(',', '\x00').replace('.', ',').replace('\x00', '.')
        return f"R$ {s}"

    _months_pt = [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    ]
    qd = quote.quote_date
    date_str = f"{qd.day} de {_months_pt[qd.month - 1]} de {qd.year}"

    # ════════════════════════════════════════════════════════════
    # PAGE 1 – COVER
    # ════════════════════════════════════════════════════════════
    _draw_bg(config.cover_image)

    c.setFillColor(WHITE)

    # "Proposta" — italic, letter-spaced
    p1_font, p1_size, p1_cs = "Helvetica-Oblique", 24, 6
    p1_y = page_h * 0.53
    _draw_spaced_centered("Proposta", page_w / 2, p1_y, p1_font, p1_size, p1_cs)

    # "COMERCIAL" — bold, larger
    p2_font, p2_size, p2_cs = "Helvetica-Bold", 58, 10
    p2_y = p1_y - p2_size - 10
    _draw_spaced_centered("COMERCIAL", page_w / 2, p2_y, p2_font, p2_size, p2_cs)

    c.showPage()

    # ════════════════════════════════════════════════════════════
    # PAGE 2 – SOBRE NÓS
    # ════════════════════════════════════════════════════════════
    _draw_bg(config.about_image)

    manifesto = [
        "A MADEIRA SEMPRE FOI MAIS DO QUE",
        "MATÉRIA-PRIMA.",
        "ELA CARREGA TEMPO, ORIGEM E",
        "TRANSFORMAÇÃO.",
        "NA ROSELAR, CADA PEÇA É ESCOLHIDA",
        "COM ESSE ENTENDIMENTO.",
        "",
        "TRABALHAMOS COM MARCAS QUE UNEM",
        "TECNOLOGIA, MADEIRAS DE LEI DE ALTA",
        "QUALIDADE E MATÉRIAS-PRIMAS",
        "PROVENIENTES DE MANEJO",
        "RESPONSÁVEL E REFLORESTAMENTO.",
        "",
        "SELECIONAMOS MÓVEIS QUE",
        "EQUILIBRAM DESIGN CONTEMPORÂNEO,",
        "ESTRUTURA E ACABAMENTO — PEÇAS",
        "PENSADAS PARA INTEGRAR O AMBIENTE",
        "COM NATURALIDADE.",
        "",
        "ACREDITAMOS QUE BONS ESPAÇOS",
        "NASCEM DE ESCOLHAS BEM FEITAS.",
        "E QUANDO A ESCOLHA É CERTA, ELA",
        "PERMANECE.",
        "",
        "MÓVEIS FEITOS PARA ACOMPANHAR",
        "DÉCADAS, ATRAVESSANDO HISTÓRIAS E",
        "GERAÇÕES.",
        "",
        "DESIGN QUE ATRAVESSA GERAÇÕES.",
    ]

    c.setFillColor(WHITE)
    y_m = page_h - 5.5 * cm
    for line in manifesto:
        if line:
            _draw_spaced(line, 2.5 * cm, y_m, "Helvetica", 9.5, cs=2.0)
        y_m -= 15.5

    # "SOBRE NÓS" label — bottom-right
    sn_text, sn_font, sn_size, sn_cs = "SOBRE NÓS", "Helvetica-Bold", 12, 4
    sn_w = _spaced_w(sn_text, sn_font, sn_size, sn_cs)
    draw_x = page_w - sn_w - 2.5 * cm
    _draw_spaced(sn_text, draw_x, 3.5 * cm, sn_font, sn_size, sn_cs)

    c.showPage()

    # ════════════════════════════════════════════════════════════
    # PAGE 3+ – ITEMS
    # ════════════════════════════════════════════════════════════
    MX       = 2.2 * cm   # horizontal margin
    MY       = 2.2 * cm   # vertical margin
    CW       = page_w - 2 * MX
    HEADER_H = 72          # header block height (client / seller / date)
    ITEM_H   = 178         # each item block height
    IMG_SZ   = 128         # product image bounding square (pt)
    FOOTER_H = 185         # Proposta Especial section height

    items = list(quote.items.prefetch_related('images').all())

    def _items_page_bg():
        c.setFillColor(LINEN)
        c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    def _draw_header():
        """Draw client / seller / date block and gold separator."""
        top = page_h - MY

        # "Cliente" label
        c.setFillColor(GRAY)
        _draw_spaced("Cliente", MX, top - 14, "Helvetica", 7.5, cs=1.0)
        # Client name
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(MX, top - 30, quote.customer.name)

        # "Consultora" label (centred)
        c.setFillColor(GRAY)
        _draw_spaced_centered("Consultora", page_w / 2, top - 14, "Helvetica", 7.5, cs=1.0)
        # Seller name (centred)
        seller_label = quote.seller.get_full_name() or quote.seller.username
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(page_w / 2, top - 30, seller_label)

        # "Data" label (right-aligned, letter-spaced)
        c.setFillColor(GRAY)
        data_w = _spaced_w("Data", "Helvetica", 7.5, 1.0)
        _draw_spaced("Data", MX + CW - data_w, top - 14, "Helvetica", 7.5, cs=1.0)
        # Date (right-aligned)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawRightString(MX + CW, top - 30, date_str)

        # Gold separator line
        sep_y = page_h - MY - HEADER_H + 10
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.2)
        c.line(MX, sep_y, MX + CW, sep_y)
        return sep_y - 15   # Y where first item should start

    def _img_placeholder(x, y, sz):
        c.setFillColor(LGRAY)
        c.rect(x, y, sz, sz, fill=1, stroke=0)
        c.setFillColor(GRAY)
        c.setFont("Helvetica", 7)
        c.drawCentredString(x + sz / 2, y + sz / 2 - 4, "sem imagem")

    def _draw_item(item, y_top, idx):
        """Draw one item block starting at y_top. Returns Y after block."""
        img_right = (idx % 2 == 0)

        if img_right:
            txt_x = MX
            txt_w = CW - IMG_SZ - 14
            img_x = MX + CW - IMG_SZ
        else:
            img_x = MX
            txt_x = MX + IMG_SZ + 14
            txt_w = CW - IMG_SZ - 14

        # Image — vertically centred in block
        img_y = y_top - (ITEM_H + IMG_SZ) / 2
        first_img = item.images.first()
        if first_img:
            try:
                c.drawImage(ImageReader(first_img.image.path),
                            img_x, img_y, width=IMG_SZ, height=IMG_SZ,
                            preserveAspectRatio=True, mask='auto')
            except Exception:
                _img_placeholder(img_x, img_y, IMG_SZ)
        else:
            _img_placeholder(img_x, img_y, IMG_SZ)

        # ── text area ──────────────────────────────────────────
        ty = y_top - 6

        # Quantity (large gold number)
        qty_str  = f"{item.quantity:02d}"
        qty_font, qty_size = "Helvetica-Bold", 32
        c.setFillColor(GOLD)
        c.setFont(qty_font, qty_size)
        c.drawString(txt_x, ty - qty_size, qty_str)

        # Product name (right of quantity)
        name_x = txt_x + _sw(qty_str, qty_font, qty_size) + 8
        c.setFillColor(NAVY)
        _draw_spaced(item.product_name.upper(), name_x, ty - 20,
                     "Helvetica-Bold", 11, cs=1.5)

        ty -= qty_size + 10   # move below quantity

        # Description lines
        if item.description:
            for raw_line in item.description.split('\n'):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                for wline in _wrap(raw_line, "Helvetica", 8.5, txt_w):
                    c.setFillColor(GRAY)
                    _draw_spaced(wline, txt_x, ty, "Helvetica", 8.5, cs=0.5)
                    ty -= 12

        # Condition text
        if item.condition_text:
            for wline in _wrap(item.condition_text.strip(), "Helvetica", 8.5, txt_w):
                c.setFillColor(GRAY)
                _draw_spaced(wline, txt_x, ty, "Helvetica", 8.5, cs=0.5)
                ty -= 12

        # Price label + value (near bottom of text area)
        price_y = y_top - ITEM_H + 26
        if item.quantity == 1:
            price_label = "valor total"
            price_amt   = item.unit_value * item.quantity
        else:
            price_label = "valor unitário"
            price_amt   = item.unit_value

        c.setFillColor(GRAY)
        _draw_spaced(price_label, txt_x, price_y + 13, "Helvetica", 7.5, cs=1.5)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(txt_x, price_y - 1, _fmt_brl(price_amt))

        # Thin grey separator at bottom of block
        bot_y = y_top - ITEM_H
        c.setStrokeColor(LGRAY)
        c.setLineWidth(0.5)
        c.line(MX, bot_y + 3, MX + CW, bot_y + 3)

        return bot_y - 5

    def _draw_proposta_especial(y_top):
        """Draw the Proposta Especial footer block."""
        # Gold top rule
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.5)
        c.line(MX, y_top, MX + CW, y_top)

        ty = y_top - 22

        # Title
        c.setFillColor(NAVY)
        _draw_spaced("PROPOSTA ESPECIAL", MX, ty, "Helvetica-Bold", 13, cs=3)
        ty -= 20

        # Validity
        c.setFillColor(GRAY)
        c.setFont("Helvetica", 8.5)
        c.drawString(MX, ty, "Orçamento válido por 03 dias.")
        ty -= 13

        # Freight note
        if quote.freight_responsible == FreightResponsible.STORE:
            c.drawString(MX, ty, "Entrega e montagem grátis pela equipe Roselar Móveis.")
            ty -= 13
        elif quote.freight_responsible == FreightResponsible.CUSTOMER:
            c.drawString(MX, ty, "Frete por conta do cliente.")
            ty -= 13

        ty -= 8

        # Financials
        subtotal = quote.calculate_subtotal()
        disc_pct = quote.discount_percent or Decimal('0')
        disc_val = subtotal * disc_pct / Decimal('100')
        avista   = subtotal - disc_val

        # À vista line (shown when there is a discount)
        if disc_pct > 0:
            c.setFillColor(NAVY)
            c.setFont("Helvetica", 9.5)
            c.drawString(MX, ty, "Valor do investimento à vista")
            c.setFont("Helvetica-Bold", 11)
            c.drawRightString(MX + CW, ty, _fmt_brl(avista))
            ty -= 18

        # Installment line
        n = quote.payment_installments or 1
        if n > 1:
            inst_val = subtotal / Decimal(n)
            c.setFillColor(GRAY)
            c.setFont("Helvetica", 8.5)
            c.drawString(MX, ty, f"OU em {n}x sem juros de {_fmt_brl(inst_val)}")
            ty -= 18

        # Gold divider before grand total
        ty -= 4
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.8)
        c.line(MX, ty, MX + CW, ty)
        ty -= 16

        # Grand total (= sum of all item lines, no discount)
        c.setFillColor(NAVY)
        c.setFont("Helvetica", 10)
        c.drawString(MX, ty, "Valor do investimento:")
        c.setFont("Helvetica-Bold", 14)
        c.drawRightString(MX + CW, ty, _fmt_brl(subtotal))

    # ── render items ─────────────────────────────────────────────
    _items_page_bg()
    cur_y = _draw_header()

    for i, item in enumerate(items):
        is_last = (i == len(items) - 1)
        space_needed = ITEM_H + (FOOTER_H if is_last else 0)

        if cur_y - space_needed < MY:
            c.showPage()
            _items_page_bg()
            cur_y = page_h - MY - 15

        cur_y = _draw_item(item, cur_y, i)

    # ── Proposta Especial ─────────────────────────────────────────
    if cur_y - FOOTER_H < MY:
        c.showPage()
        _items_page_bg()
        cur_y = page_h - MY

    _draw_proposta_especial(cur_y - 10)

    # ── finalise ──────────────────────────────────────────────────
    c.save()
    pdf = buffer.getvalue()
    buffer.close()

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="proposta_{quote.number}.pdf"'
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
    
    # Delivery weeks (estimated)
    if quote.delivery_weeks:
        elements.append(Spacer(1, 0.3*cm))
        semanas = 'semana' if quote.delivery_weeks == 1 else 'semanas'
        elements.append(Paragraph(f"<b>Prazo de Entrega Estimado:</b> {quote.delivery_weeks} {semanas}", normal_style))
    
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
    
    # Calculate total
    total = sum(item.line_total for item in items)
    
    context = {
        'order': order,
        'items': items,
        'total': total,
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
    
    # Delivery deadline (real date on order)
    if order.delivery_deadline:
        elements.append(Spacer(1, 0.3*cm))
        elements.append(Paragraph(f"<b>Prazo de Entrega:</b> {order.delivery_deadline.strftime('%d/%m/%Y')}", normal_style))
    elif order.quote.delivery_weeks:
        semanas = 'semana' if order.quote.delivery_weeks == 1 else 'semanas'
        elements.append(Spacer(1, 0.3*cm))
        elements.append(Paragraph(f"<b>Prazo de Entrega Estimado:</b> {order.quote.delivery_weeks} {semanas}", normal_style))
    
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


@login_required
@require_http_methods(["GET", "POST"])
def quote_simulate_commission(request: HttpRequest, quote_id: int) -> HttpResponse: 
    from core.models import PaymentTariff, PaymentMethodType, ArchitectCommission

    quote = get_object_or_404(
        Quote.objects.select_related('customer', 'seller'), id=quote_id
    )

    subtotal = quote.calculate_subtotal()
    freight_value = quote.freight_value or Decimal("0.00")

    # ── Constants ─────────────────────────────────────────────────────────────
    MAX_DISCOUNT_NORMAL   = Decimal("10")   # above this → penalty zone
    MAX_DISCOUNT_ABSOLUTE = Decimal("15")   # hard ceiling

    # ── Parse inputs ──────────────────────────────────────────────────────────
    if request.method == "POST":
        sim_payment_type    = request.POST.get('sim_payment_type', '') or ''
        sim_has_architect   = request.POST.get('sim_has_architect') == '1'
        sim_discount        = Decimal(request.POST.get('discount_percent', '0') or '0')
        try:
            price_increase_pct = Decimal(request.POST.get('price_increase_percent', '0') or '0')
        except Exception:
            price_increase_pct = Decimal('0')
        sim_installments    = max(1, int(request.POST.get('sim_installments', '1') or 1))
    else:
        sim_payment_type    = quote.payment_type or ''
        sim_has_architect   = quote.has_architect
        sim_discount        = quote.discount_percent or Decimal("0")
        price_increase_pct  = Decimal('0')
        sim_installments    = quote.payment_installments or 1

    # Continuous threshold — accepts any float, no whitelist
    price_increase_pct = max(Decimal('0'), min(price_increase_pct, Decimal('10')))
    if price_increase_pct >= 10:
        max_installments_unlocked = 18
    elif price_increase_pct >= 5:
        max_installments_unlocked = 14
    elif price_increase_pct >= 3:
        max_installments_unlocked = 10
    else:
        max_installments_unlocked = 7

    # Clamp discount and installments to their allowed ranges
    sim_discount     = max(Decimal("0"), min(sim_discount, MAX_DISCOUNT_ABSOLUTE))
    sim_installments = min(sim_installments, max_installments_unlocked)
    sim_installments = max(1, sim_installments)

    # ── Commission from discount ──────────────────────────────────────────────
    if sim_discount <= MAX_DISCOUNT_NORMAL:
        # Linear: 5 % at 0 %, 3 % at 10 % → step = −0.2 % per 1 %
        comm_discount    = max(Decimal("3"), Decimal("5") - sim_discount / Decimal("5"))
        discount_penalty = False
    else:
        # Dinheiro/PIX: metade da penalidade
        comm_discount    = Decimal("2.5") if sim_payment_type in ("CASH", "PIX") else Decimal("2")
        discount_penalty = True

    # ── Commission from installments ──────────────────────────────────────────
    # 1x=5%, 2x=4%, 3-7x linear to 3%, 8x+=2% (penalty, unlocked by price increase)
    if sim_installments <= 1:
        comm_installments   = Decimal("5")
        installment_penalty = False
    elif sim_installments == 2:
        comm_installments   = Decimal("4")
        installment_penalty = False
    elif sim_installments <= 7:
        comm_installments   = max(
            Decimal("3"),
            Decimal("4") - Decimal("0.2") * (sim_installments - 2),
        )
        installment_penalty = False
    else:
        # 8x+: only reachable with price increase; dinheiro/PIX = metade da penalidade
        comm_installments   = Decimal("2.5") if sim_payment_type in ("CASH", "PIX") else Decimal("2")
        installment_penalty = True

    # ── Final commission = lower of the two ──────────────────────────────────
    seller_commission_percent = min(comm_discount, comm_installments)
    is_penalty                = discount_penalty or installment_penalty

    # ── Payment fee ───────────────────────────────────────────────────────────
    payment_fee_percent = (
        Decimal(str(PaymentTariff.get_fee(sim_payment_type, sim_installments)))
        if sim_payment_type else Decimal("0")
    )

    # ── Price-adjusted subtotal ───────────────────────────────────────────────
    price_multiplier  = (Decimal("100") + price_increase_pct) / Decimal("100")
    adj_subtotal      = subtotal * price_multiplier
    total_before_disc = adj_subtotal + freight_value
    discount_value    = total_before_disc * sim_discount / Decimal("100")
    total_after_disc  = total_before_disc - discount_value
    payment_fee_value = total_after_disc * payment_fee_percent / Decimal("100")
    final_total       = total_after_disc + payment_fee_value
    installment_value = (final_total / Decimal(sim_installments)) if sim_installments else final_total

    # ── Architect commission ──────────────────────────────────────────────────
    architect_percent          = Decimal("0")
    architect_commission_value = Decimal("0")
    if sim_has_architect:
        architect_percent          = ArchitectCommission.get_commission()
        architect_commission_value = adj_subtotal * architect_percent / Decimal("100")

    # ── Seller commission base (always over subtotal logic) ──────────────────
    # Base uses adjusted subtotal, then applies discount and payment fee impact.
    # Freight is not part of seller commission base.
    seller_base_before_discount = adj_subtotal
    seller_discount_value = seller_base_before_discount * sim_discount / Decimal("100")
    seller_base_after_discount = seller_base_before_discount - seller_discount_value
    seller_payment_fee_value = seller_base_after_discount * payment_fee_percent / Decimal("100")

    seller_commission_base = seller_base_after_discount + seller_payment_fee_value
    if sim_has_architect:
        seller_commission_base -= architect_commission_value
    seller_commission_base = max(Decimal("0"), seller_commission_base)

    seller_commission_value = seller_commission_base * seller_commission_percent / Decimal("100")

    # ── Save action ───────────────────────────────────────────────────────────
    if request.method == "POST" and request.POST.get('action') == 'save_conditions':
        with transaction.atomic():
            quote.discount_percent     = sim_discount
            quote.payment_type         = sim_payment_type
            quote.payment_installments = sim_installments
            quote.payment_fee_percent  = payment_fee_percent
            quote.has_architect        = sim_has_architect
            quote.save()
        messages.success(request, f"Condições do orçamento {quote.number} salvas com sucesso.")
        return redirect("sales:quote_detail", quote_id=quote.id)

    # Non-AJAX POST — redirect to GET to prevent browser resubmission dialog
    if request.method == "POST" and not request.POST.get('_ajax'):
        return redirect(request.path)

    # ── Build payment selects (tariffs up to 18 x) ────────────────────────────
    payment_type_choices = list(PaymentMethodType.choices)
    max_inst_map = {
        'CASH': 1, 'PIX': 1, 'CREDIT_CARD': 18, 'CHEQUE': 1, 'BOLETO': 18,
    }
    tariffs_by_type: dict[str, list] = {}
    for pt_val, _pt_lbl in payment_type_choices:
        max_inst = max_inst_map.get(pt_val, 1)
        existing = {
            t.installments: float(t.fee_percent)
            for t in PaymentTariff.objects.filter(payment_type=pt_val)
        }
        options = []
        for i in range(1, max_inst + 1):
            fee = existing.get(i, 0)
            lbl = ("À vista" if i == 1 else f"{i}x") + (f" (+{fee}%)" if fee else "")
            options.append({'installments': i, 'fee': fee, 'label': lbl})
        tariffs_by_type[pt_val] = options

    # Payment description
    if sim_payment_type:
        pt_label = dict(PaymentMethodType.choices).get(sim_payment_type, sim_payment_type)
        sim_payment_description = (
            f"{pt_label} - À vista" if sim_installments == 1
            else f"{pt_label} - {sim_installments}x"
        )
    else:
        sim_payment_description = "Não definido"

    context = {
        'quote':                    quote,
        # Subtotals
        'subtotal':                 subtotal,
        'adj_subtotal':             adj_subtotal,
        'freight_value':            freight_value,
        'price_increase_pct':       price_increase_pct,
        'total_before_discount':    total_before_disc,
        'discount_percent':         sim_discount,
        'discount_value':           discount_value,
        'total_after_discount':     total_after_disc,
        'payment_fee_percent':      payment_fee_percent,
        'payment_fee_value':        payment_fee_value,
        'final_total':              final_total,
        'installment_value':        installment_value,
        # Commission
        'seller_commission_percent': seller_commission_percent,
        'seller_commission_value':   seller_commission_value,
        'seller_commission_base':    seller_commission_base,
        'comm_discount':             comm_discount,
        'comm_installments':         comm_installments,
        'is_penalty':                is_penalty,
        'discount_penalty':          discount_penalty,
        'installment_penalty':       installment_penalty,
        # Architect
        'architect_percent':          architect_percent,
        'architect_commission_value': architect_commission_value,
        # Limits
        'max_discount_normal':       MAX_DISCOUNT_NORMAL,
        'max_discount_absolute':     MAX_DISCOUNT_ABSOLUTE,
        'max_installments_unlocked': max_installments_unlocked,
        # Payment
        'payment_type_choices':    payment_type_choices,
        'sim_payment_type':        sim_payment_type,
        'sim_installments':        sim_installments,
        'tariffs_by_type_json':    json.dumps(tariffs_by_type),
        'sim_payment_description': sim_payment_description,
        'sim_has_architect':       sim_has_architect,
        'is_cash_pix':             sim_payment_type in ('CASH', 'PIX'),
        # Price increase options
        'price_increase_options': [
            {'value': '0',  'max_inst': 7,  'label': 'Sem aumento',   'desc': 'até 7x'},
            {'value': '3',  'max_inst': 10, 'label': '+3% no valor',  'desc': 'até 10x'},
            {'value': '5',  'max_inst': 14, 'label': '+5% no valor',  'desc': 'até 14x'},
            {'value': '10', 'max_inst': 18, 'label': '+10% no valor', 'desc': 'até 18x'},
        ],
    }

    context['max_installments_range'] = range(1, max_installments_unlocked + 1)

    return render(request, 'sales/quote_simulation.html', context)


# ──────────────────────────────────────────────────────────────────────
# Duplicate Quote
# ──────────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["POST"])
def quote_duplicate(request, quote_id):
    """Duplicar um orçamento existente."""
    original = get_object_or_404(Quote, id=quote_id)
    from core.models import AuditLog, AuditAction

    with transaction.atomic():
        new_number = generate_next_quote_number()
        new_quote = Quote.objects.create(
            number=new_number,
            customer=original.customer,
            seller=request.user,
            status=QuoteStatus.DRAFT,
            quote_date=timezone.localdate(),
            freight_value=original.freight_value,
            freight_responsible=original.freight_responsible,
            shipping_company=original.shipping_company,
            shipping_payment_method=original.shipping_payment_method,
            discount_percent=original.discount_percent,
            has_architect=original.has_architect,
            payment_type=original.payment_type,
            payment_installments=original.payment_installments,
            payment_fee_percent=original.payment_fee_percent,
        )

        for item in original.items.all():
            QuoteItem.objects.create(
                quote=new_quote,
                supplier=item.supplier,
                product_name=item.product_name,
                description=item.description,
                quantity=item.quantity,
                unit_value=item.unit_value,
                condition_text=item.condition_text,
                architect_percent=item.architect_percent,
            )

    AuditLog.log(request.user, AuditAction.CREATE_QUOTE,
                 f"Orçamento duplicado: {original.number} → {new_quote.number}", obj=new_quote)

    messages.success(request, f"Orçamento duplicado: {new_quote.number}")
    return redirect("sales:quote_edit", quote_id=new_quote.id)
