from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING
from io import BytesIO
from urllib.parse import quote as url_quote

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

logger = logging.getLogger(__name__)


def _safe_content_disposition(filename: str) -> str:
    """Build a Content-Disposition header value safe for any filename."""
    # ASCII-safe fallback: strip accents, replace non-ASCII
    nfkd = unicodedata.normalize('NFKD', filename)
    ascii_name = nfkd.encode('ascii', 'ignore').decode('ascii')
    ascii_name = re.sub(r'[^\w.\-]', '_', ascii_name)
    # RFC 6266: filename for ASCII, filename* for UTF-8
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{url_quote(filename)}"
    )


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
    try:
        c.save()
    except Exception:
        logger.exception('Error generating client PDF for quote %s', quote.number)
        raise
    pdf = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = _safe_content_disposition(
        f'proposta_{quote.number}.pdf'
    )
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
    try:
        doc.build(elements)
    except Exception:
        logger.exception('Error generating supplier PDF for quote %s', quote.number)
        raise
    
    # Get PDF from buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create response
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = _safe_content_disposition(
        f'orcamento_{quote.number}_fornecedor.pdf'
    )
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
    
    # Build PDF
    try:
        doc.build(elements)
    except Exception:
        logger.exception('Error generating order PDF %s', order.number)
        raise
    
    # Get PDF from buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create response
    supplier_name = order.supplier.name if order.supplier else 'sem_fornecedor'
    filename = f'pedido_{order.number}_{supplier_name}.pdf'
    filename = filename.replace(' ', '_').replace('/', '_')
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = _safe_content_disposition(filename)
    return response


# ──────────────────────────────────────────────────────────────────────
# Shared simulation logic
# ──────────────────────────────────────────────────────────────────────
def _build_simulation_context(
    *,
    subtotal: Decimal,
    freight_value: Decimal,
    sim_payment_type: str,
    sim_has_architect: bool,
    sim_discount: Decimal,
    price_increase_pct: Decimal,
    sim_installments: int,
) -> dict:
    """Pure calculation — no DB writes, no request handling."""
    from core.models import PaymentTariff, PaymentMethodType, ArchitectCommission

    MARGIN_BASE = Decimal("10")

    COMMISSION_FLOOR = Decimal("2")   # when margin is exceeded
    COMMISSION_MIN   = Decimal("3")
    COMMISSION_MAX   = Decimal("5")
    MAX_DISCOUNT_ABSOLUTE = Decimal("30")
    MAX_MARGIN_EXCESS = Decimal("5")   # margin can go at most 5% below
    FEE_STORE_MAX_INSTALLMENTS = 12  # store absorbs fee up to this many installments

    # Clamp
    price_increase_pct = max(Decimal('0'), min(price_increase_pct, Decimal('30')))
    sim_discount       = max(Decimal("0"), min(sim_discount, MAX_DISCOUNT_ABSOLUTE))
    sim_installments   = max(1, min(sim_installments, 18))

    # Interest / fee
    payment_fee_percent = (
        Decimal(str(PaymentTariff.get_fee(sim_payment_type, sim_installments)))
        if sim_payment_type else Decimal("0")
    )

    # For 13-18x: store absorbs up to the 12x fee; client pays the difference
    store_fee_percent = payment_fee_percent  # store's cost (capped at 12x rate)
    client_surcharge_percent = Decimal("0")
    if sim_installments > FEE_STORE_MAX_INSTALLMENTS and sim_payment_type:
        fee_12x = Decimal(str(PaymentTariff.get_fee(sim_payment_type, FEE_STORE_MAX_INSTALLMENTS)))
        if payment_fee_percent > fee_12x:
            client_surcharge_percent = payment_fee_percent - fee_12x
            store_fee_percent = fee_12x

    # Architect — always loaded (immutable, non-negotiable when present)
    architect_percent = ArchitectCommission.get_commission()
    architect_commission_value = Decimal("0")
    # Architect cost in margin: only when client has architect, but always at full rate
    architect_cost_pct = architect_percent if sim_has_architect else Decimal("0")

    # Effective margin and max discount allowed
    # Margin can go at most MAX_MARGIN_EXCESS below zero.
    # With commission at COMMISSION_FLOOR (worst case):
    #   total_cost = store_fee + architect + discount + COMMISSION_FLOOR
    #   total_cost - effective_margin <= MAX_MARGIN_EXCESS
    #   → discount <= effective_margin + MAX_MARGIN_EXCESS - store_fee - architect - COMMISSION_FLOOR
    effective_margin = MARGIN_BASE + price_increase_pct
    allowance = effective_margin + MAX_MARGIN_EXCESS - store_fee_percent - architect_cost_pct - COMMISSION_FLOOR
    max_discount_allowed = max(Decimal("0"), min(MAX_DISCOUNT_ABSOLUTE, allowance))

    # Re-clamp discount to max_discount_allowed
    sim_discount = min(sim_discount, max_discount_allowed)

    # Dynamic commission based on margin room
    # room = what's left in effective margin after fixed costs (fee, architect, discount)
    # Commission = room/2 clamped to [3%, 5%]: splits available space evenly
    # between commission and remaining profit margin
    fixed_costs = store_fee_percent + architect_cost_pct + sim_discount
    room = effective_margin - fixed_costs

    # Commission = half of room, clamped between 3% and 5%
    # Then check: if margin is exceeded with that commission → drop to 2%
    seller_commission_percent = min(COMMISSION_MAX, max(COMMISSION_MIN, room / 2))
    total_cost_pct = fixed_costs + seller_commission_percent
    if total_cost_pct > effective_margin:
        seller_commission_percent = COMMISSION_FLOOR
    original_commission_percent = COMMISSION_MAX  # for display: max possible

    # Total cost and margin check
    total_cost_pct  = fixed_costs + seller_commission_percent
    margin_exceeded = total_cost_pct > effective_margin
    commission_reduced = seller_commission_percent < COMMISSION_MAX

    margin_excess = total_cost_pct - effective_margin if margin_exceeded else Decimal("0")
    controls_blocked = margin_excess >= MAX_MARGIN_EXCESS

    # Suggested increase: how much MORE to increase price to eliminate the excess
    suggested_increase = Decimal("0")
    if margin_exceeded:
        suggested_increase = (margin_excess * 2).quantize(Decimal("1"), rounding=ROUND_CEILING) / 2

    # Price-adjusted subtotal
    price_multiplier  = (Decimal("100") + price_increase_pct) / Decimal("100")
    adj_subtotal      = subtotal * price_multiplier
    total_before_disc = adj_subtotal + freight_value
    discount_value    = total_before_disc * sim_discount / Decimal("100")
    total_after_disc  = total_before_disc - discount_value

    # Card fee: store absorbs store_fee_percent; client pays client_surcharge_percent
    payment_fee_value = total_after_disc * store_fee_percent / Decimal("100")
    client_surcharge_value = total_after_disc * client_surcharge_percent / Decimal("100")

    # Client pays total_after_disc + surcharge for 13-18x
    final_total       = total_after_disc + client_surcharge_value
    installment_value = (final_total / Decimal(sim_installments)) if sim_installments else final_total

    # valor_avista: what the store actually receives (client total minus ALL card fees)
    valor_avista = final_total - payment_fee_value - client_surcharge_value

    # Architect commission (based on what the store actually receives)
    if sim_has_architect:
        architect_commission_value = valor_avista * architect_percent / Decimal("100")

    # Seller commission base
    seller_commission_base = adj_subtotal
    seller_discount_value  = seller_commission_base * sim_discount / Decimal("100")
    seller_commission_base = seller_commission_base - seller_discount_value
    if sim_has_architect:
        seller_commission_base -= architect_commission_value
    seller_commission_base  = max(Decimal("0"), seller_commission_base)
    seller_commission_value = seller_commission_base * seller_commission_percent / Decimal("100")

    # Payment selects
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
            if i == 1:
                lbl = "À vista"
            elif i <= FEE_STORE_MAX_INSTALLMENTS:
                lbl = f"{i}x sem juros"
            else:
                lbl = f"{i}x com juros"
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

    return {
        'subtotal':                 subtotal,
        'adj_subtotal':             adj_subtotal,
        'freight_value':            freight_value,
        'price_increase_pct':       price_increase_pct,
        'total_before_discount':    total_before_disc,
        'discount_percent':         sim_discount,
        'discount_value':           discount_value,
        'total_after_discount':     total_after_disc,
        'payment_fee_percent':      payment_fee_percent,
        'store_fee_percent':        store_fee_percent,
        'payment_fee_value':        payment_fee_value,
        'client_surcharge_percent': client_surcharge_percent,
        'client_surcharge_value':   client_surcharge_value,
        'final_total':              final_total,
        'installment_value':        installment_value,
        'seller_commission_percent': seller_commission_percent,
        'seller_commission_value':   seller_commission_value,
        'seller_commission_base':    seller_commission_base,
        'original_commission_percent': original_commission_percent,
        'commission_reduced':          commission_reduced,
        'margin_base':               MARGIN_BASE,
        'effective_margin':          effective_margin,
        'total_cost_pct':            total_cost_pct,
        'margin_exceeded':           margin_exceeded,
        'margin_excess':             margin_excess,
        'controls_blocked':          controls_blocked,
        'max_discount_allowed':      max_discount_allowed,

        'suggested_increase':        suggested_increase,
        'architect_percent':          architect_percent,
        'architect_commission_value': architect_commission_value,
        'valor_avista':               valor_avista,
        'max_discount_absolute':     MAX_DISCOUNT_ABSOLUTE,
        'payment_type_choices':      payment_type_choices,
        'sim_payment_type':          sim_payment_type,
        'sim_installments':          sim_installments,
        'tariffs_by_type_json':      json.dumps(tariffs_by_type),
        'sim_payment_description':   sim_payment_description,
        'sim_has_architect':         sim_has_architect,
    }


@login_required
@require_http_methods(["GET", "POST"])
def quote_simulate_commission(request: HttpRequest, quote_id: int) -> HttpResponse:
    quote = get_object_or_404(
        Quote.objects.select_related('customer', 'seller'), id=quote_id
    )

    subtotal = quote.calculate_subtotal()
    freight_value = quote.freight_value or Decimal("0.00")

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

    ctx = _build_simulation_context(
        subtotal=subtotal,
        freight_value=freight_value,
        sim_payment_type=sim_payment_type,
        sim_has_architect=sim_has_architect,
        sim_discount=sim_discount,
        price_increase_pct=price_increase_pct,
        sim_installments=sim_installments,
    )

    if request.method == "POST" and request.POST.get('action') == 'save_conditions':
        with transaction.atomic():
            quote.discount_percent     = ctx['discount_percent']
            quote.payment_type         = ctx['sim_payment_type']
            quote.payment_installments = ctx['sim_installments']
            quote.payment_fee_percent  = ctx['payment_fee_percent']
            quote.has_architect        = ctx['sim_has_architect']
            quote.save()
        messages.success(request, f"Condições do orçamento {quote.number} salvas com sucesso.")
        return redirect("sales:quote_detail", quote_id=quote.id)

    if request.method == "POST" and not request.POST.get('_ajax'):
        return redirect(request.path)

    ctx['quote'] = quote
    return render(request, 'sales/quote_simulation.html', ctx)


# ──────────────────────────────────────────────────────────────────────
# Standalone Simulation (sem orçamento)
# ──────────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["GET", "POST"])
def standalone_simulation(request: HttpRequest) -> HttpResponse:
    """Simulação rápida de valores — sem precisar criar orçamento."""
    from core.models import Customer

    if request.method == "POST":
        try:
            subtotal = Decimal(request.POST.get('sim_subtotal', '0') or '0')
        except Exception:
            subtotal = Decimal('0')
        try:
            freight_value = Decimal(request.POST.get('sim_freight', '0') or '0')
        except Exception:
            freight_value = Decimal('0')
        sim_payment_type  = request.POST.get('sim_payment_type', '') or ''
        sim_has_architect = request.POST.get('sim_has_architect') == '1'
        try:
            sim_discount = Decimal(request.POST.get('discount_percent', '0') or '0')
        except Exception:
            sim_discount = Decimal('0')
        try:
            price_increase_pct = Decimal(request.POST.get('price_increase_percent', '0') or '0')
        except Exception:
            price_increase_pct = Decimal('0')
        sim_installments = max(1, int(request.POST.get('sim_installments', '1') or 1))
        customer_id = request.POST.get('sim_customer_id', '') or ''
    else:
        subtotal           = Decimal('0')
        freight_value      = Decimal('0')
        sim_payment_type   = ''
        sim_has_architect  = False
        sim_discount       = Decimal('0')
        price_increase_pct = Decimal('0')
        sim_installments   = 1
        customer_id        = ''

    subtotal      = max(Decimal('0'), subtotal)
    freight_value = max(Decimal('0'), freight_value)

    if request.method == "POST" and not request.POST.get('_ajax'):
        return redirect(request.path)

    # Resolve customer
    selected_customer = None
    if customer_id:
        try:
            selected_customer = Customer.objects.get(pk=int(customer_id))
        except (Customer.DoesNotExist, ValueError, TypeError):
            pass

    ctx = _build_simulation_context(
        subtotal=subtotal,
        freight_value=freight_value,
        sim_payment_type=sim_payment_type,
        sim_has_architect=sim_has_architect,
        sim_discount=sim_discount,
        price_increase_pct=price_increase_pct,
        sim_installments=sim_installments,
    )
    ctx['standalone']         = True
    ctx['sim_subtotal']       = subtotal
    ctx['sim_freight']        = freight_value
    ctx['selected_customer']  = selected_customer
    ctx['sim_customer_id']    = customer_id

    return render(request, 'sales/standalone_simulation.html', ctx)


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
