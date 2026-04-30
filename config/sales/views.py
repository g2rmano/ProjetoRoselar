from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import defaultdict
from datetime import date as date_type, time as time_type, timedelta
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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

logger = logging.getLogger(__name__)


def _is_admin(user):
    """True for ADMIN or superuser."""
    from accounts.models import Role
    return user.is_superuser or user.role == Role.ADMIN


def _is_finance(user):
    """True for FINANCE role (non-admin)."""
    from accounts.models import Role
    return (not user.is_superuser) and user.role == Role.FINANCE


def _is_staff_or_admin(user):
    """True for ADMIN or superuser (full quote visibility)."""
    from accounts.models import Role
    return user.is_superuser or user.role == Role.ADMIN


def _is_seller(user):
    """True only for SELLER role (no elevated permissions)."""
    from accounts.models import Role
    return user.role == Role.SELLER and not user.is_superuser


def _can_view_all_orders(user):
    """Admins and finance can view all purchase orders."""
    return _is_admin(user) or _is_finance(user)


def _can_generate_order_pdf(user):
    """Admins and finance can generate supplier purchase-order PDFs."""
    return _is_admin(user) or _is_finance(user)


def _can_view_commission(user):
    """Finance cannot see commission values."""
    return not _is_finance(user)


def _get_quote_or_403(request, quote_id, **extra_filters):
    """Fetch a quote by ID; sellers can only see their own."""
    from django.http import HttpResponseForbidden
    quote = get_object_or_404(Quote, id=quote_id, **extra_filters)
    if not _is_staff_or_admin(request.user) and quote.seller_id != request.user.id:
        return None, HttpResponseForbidden("Acesso negado.")
    return quote, None


def _get_order_or_403(request, order_id, **extra_filters):
    """Fetch an order by ID; sellers can only see their own."""
    from django.http import HttpResponseForbidden
    order = get_object_or_404(Order, pk=order_id, **extra_filters)
    if not _can_view_all_orders(request.user) and order.quote.seller_id != request.user.id:
        return None, HttpResponseForbidden("Acesso negado.")
    return order, None


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


def _persist_item_images_from_formset(formset) -> None:
    """Save uploaded item images from form-only field into QuoteItemImage model."""
    for item_form in formset.forms:
        if not hasattr(item_form, "cleaned_data"):
            continue
        if item_form.cleaned_data.get("DELETE"):
            continue

        item = item_form.instance
        if not item or not item.pk:
            continue

        uploaded_image = item_form.cleaned_data.get("item_image")
        if not uploaded_image:
            continue

        # Keep a single current image per item to avoid stale/duplicate images.
        existing_images = QuoteItemImage.objects.filter(item=item)
        for old_image in existing_images:
            if old_image.image:
                try:
                    old_image.image.delete(save=False)
                except Exception:
                    pass
        existing_images.delete()

        QuoteItemImage.objects.create(item=item, image=uploaded_image)


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
    CalendarEvent,
    EventStatus,
    EventType,
    Reminder,
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
        'DEBIT_CARD': 1,
        'CREDIT_CARD': 18,
        'CHEQUE': 1,
        'BOLETO': 18,
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
        username = data.get('username', '').strip()
        password = data.get('password')
        discount = data.get('discount')
        
        if not password or discount is None:
            return JsonResponse({'authorized': False, 'error': 'Missing parameters'}, status=400)
        
        discount_value = float(discount)
        
        if discount_value <= 15:
            return JsonResponse({'authorized': False, 'error': 'Discount must be > 15%'}, status=400)
        
        # Authenticate: current user if no username provided, or specific admin
        target_username = username if username else request.user.username
        user = authenticate(username=target_username, password=password)
        
        if user and user.is_staff:
            return JsonResponse({
                'authorized': True,
                'authorized_by': user.username,
                'discount': discount_value
            })
        
        return JsonResponse({'authorized': False, 'error': 'Credenciais inválidas'}, status=403)
        
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'authorized': False, 'error': 'Dados inválidos'}, status=400)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('authorize_discount_api error')
        return JsonResponse({'authorized': False, 'error': 'Erro interno'}, status=500)


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
    if not _is_staff_or_admin(request.user):
        quotes = quotes.filter(seller=request.user)
    
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
                _persist_item_images_from_formset(formset)

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
    if not _is_staff_or_admin(request.user) and quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:quote_list")

    if request.method == "POST":
        form = QuoteForm(request.POST, instance=quote)
        formset = QuoteItemFormSet(request.POST, request.FILES, instance=quote)

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
                _persist_item_images_from_formset(formset)

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
    quote = get_object_or_404(
        Quote.objects
        .select_related("customer", "seller")
        .prefetch_related("items", "items__supplier", "orders", "orders__items"),
        id=quote_id,
    )
    if not _is_staff_or_admin(request.user) and quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:quote_list")

    return render(request, "sales/quote_detail.html", {
        "quote": quote,
        "today": timezone.localdate(),
        "is_seller": _is_seller(request.user),
        "is_finance": _is_finance(request.user),
        "is_admin": _is_admin(request.user),
        "can_generate_order_pdf": _can_generate_order_pdf(request.user),
        "can_view_supplier_pdf": _is_admin(request.user),
    })


@login_required
@require_http_methods(["GET", "POST"])
def quote_reminders(request: HttpRequest, quote_id: int) -> HttpResponse:
    """
    Cria lembretes vinculados ao orçamento.
    Permite cadastrar um ou mais lembretes de uma vez.
    """
    quote = get_object_or_404(
        Quote.objects.select_related("customer", "seller"),
        id=quote_id,
    )
    if not _is_staff_or_admin(request.user) and quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:quote_list")

    # Limpa follow-ups automáticos legados para manter somente o fluxo manual.
    auto_followup_titles = [
        "Follow-up: orçamento sem resposta (3 dias)",
        "Follow-up: orçamento ainda em rascunho (7 dias)",
    ]
    CalendarEvent.objects.filter(
        quote=quote,
        event_type=EventType.QUOTE_FOLLOWUP,
        title__in=auto_followup_titles,
    ).delete()

    if request.method == "POST":
        titles = request.POST.getlist("reminder_title[]")
        dates = request.POST.getlist("reminder_date[]")
        times = request.POST.getlist("reminder_time[]")
        descriptions = request.POST.getlist("reminder_description[]")

        created_count = 0
        invalid_rows: list[int] = []

        total_rows = max(len(titles), len(dates), len(times), len(descriptions))
        for idx in range(total_rows):
            title = (titles[idx] if idx < len(titles) else "").strip()
            date_str = (dates[idx] if idx < len(dates) else "").strip()
            time_str = (times[idx] if idx < len(times) else "").strip()
            description = (descriptions[idx] if idx < len(descriptions) else "").strip()

            if not title and not date_str and not time_str and not description:
                continue

            if not title or not date_str:
                invalid_rows.append(idx + 1)
                continue

            try:
                event_date = date_type.fromisoformat(date_str)
            except ValueError:
                invalid_rows.append(idx + 1)
                continue

            event_time = None
            if time_str:
                try:
                    event_time = time_type.fromisoformat(time_str)
                except ValueError:
                    invalid_rows.append(idx + 1)
                    continue

            event = CalendarEvent.objects.create(
                title=title,
                description=description,
                event_type=EventType.QUOTE_FOLLOWUP,
                status=EventStatus.PENDING,
                event_date=event_date,
                event_time=event_time,
                assigned_to=quote.seller,
                quote=quote,
                customer=quote.customer,
            )

            Reminder.objects.create(
                event=event,
                remind_date=event_date,
                message=title,
            )
            created_count += 1

        if invalid_rows:
            messages.warning(
                request,
                f"Algumas linhas foram ignoradas por dados inválidos: {', '.join(str(x) for x in invalid_rows)}.",
            )

        if created_count > 0:
            messages.success(request, f"{created_count} lembrete(s) criado(s) com sucesso.")
            return redirect("sales:quote_reminders", quote_id=quote.id)

        if not invalid_rows:
            messages.error(request, "Preencha ao menos um lembrete com título e data.")

    reminder_events = (
        CalendarEvent.objects.filter(quote=quote, event_type=EventType.QUOTE_FOLLOWUP)
        .select_related("assigned_to")
        .prefetch_related("reminders")
        .order_by("event_date", "event_time", "id")
    )

    return render(request, "sales/quote_reminders.html", {
        "quote": quote,
        "reminder_events": reminder_events,
        "is_admin": _is_admin(request.user),
    })


@login_required
@require_http_methods(["POST"])
def quote_convert_to_orders(request: HttpRequest, quote_id: int) -> HttpResponse:
    """
    Converte orçamento -> pedido (por fornecedor + 1 total de conferência).
    O vendedor converte sem data de entrega — o financeiro define depois.
    """
    quote = get_object_or_404(
        Quote.objects.prefetch_related("items", "items__supplier"),
        id=quote_id,
    )
    if not _is_staff_or_admin(request.user) and quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:quote_list")

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

            # 4) criar pedido por fornecedor (sem data de entrega — financeiro define depois)
            for supplier_id, supplier_items in by_supplier.items():
                order = Order.objects.create(
                    number=quote.number,
                    quote=quote,
                    supplier_id=supplier_id,
                    is_total_conference=False,
                    status="OPEN",
                    delivery_deadline=None,
                )
                created_orders.append(order)

                OrderItem.objects.bulk_create([
                    OrderItem(
                        order=order,
                        product_name=it.product_name,
                        description=it.description,
                        quantity=it.quantity,
                        purchase_unit_cost=it.unit_value,
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
                delivery_deadline=None,
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

            # 6.1) lembrete automático de pagamento de arquiteto (30 dias após emissão)
            if quote.has_architect:
                reminder_date = timezone.localdate() + timedelta(days=30)
                architect_label = quote.architect.name if quote.architect_id else "Arquiteto"
                reminder_title = f"Pagamento arquiteto - Orçamento {quote.number}"
                reminder_description = (
                    f"Lembrar pagamento do arquiteto ({architect_label}) referente ao orçamento {quote.number}."
                )

                from django.contrib.auth import get_user_model
                User = get_user_model()
                recipients = [
                    user for user in User.objects.filter(is_active=True)
                    if _can_view_all_orders(user)
                ]

                for recipient in recipients:
                    event, created = CalendarEvent.objects.get_or_create(
                        quote=quote,
                        order=total_order,
                        event_type=EventType.ARCHITECT_PAYMENT,
                        assigned_to=recipient,
                        defaults={
                            "title": reminder_title,
                            "description": reminder_description,
                            "status": EventStatus.PENDING,
                            "event_date": reminder_date,
                            "customer": quote.customer,
                        },
                    )
                    if created:
                        Reminder.objects.create(
                            event=event,
                            remind_date=reminder_date,
                            message=reminder_title,
                        )

            # 7) limpar imagens temporárias (arquivo + registro)
            imgs = QuoteItemImage.objects.filter(item__quote=quote)
            for img in imgs:
                if img.image:
                    try:
                        img.image.delete(save=False)
                    except Exception:
                        pass
            imgs.delete()

        # Audit log + notificação para todos os financeiros/admins (para processarem o pedido)
        from core.models import AuditLog, AuditAction, Notification, NotificationType
        AuditLog.log(request.user, AuditAction.CONVERT_ORDER,
                     f"Orçamento {quote.number} convertido em pedido", obj=quote,
                     ip_address=request.META.get('REMOTE_ADDR'))

        # Notifica o próprio vendedor
        if quote.seller != request.user:
            Notification.send(
                quote.seller,
                f"Pedido gerado: {quote.number}",
                NotificationType.ORDER_CONFIRMED,
                message=f"Orçamento {quote.number} (cliente: {quote.customer.name}) foi convertido em pedido.",
                url=f"/sales/quotes/{quote.id}/",
            )

        # Notifica financeiros/admins para processarem (baixar PDFs e definir data de entrega)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        for finance_user in User.objects.filter(is_active=True):
            if _can_view_all_orders(finance_user) and finance_user != request.user:
                Notification.send(
                    finance_user,
                    f"Novo pedido aguardando: {quote.number}",
                    NotificationType.ORDER_CONFIRMED,
                    message=(
                        f"Orçamento {quote.number} (cliente: {quote.customer.name}) "
                        f"foi convertido em pedido pelo vendedor {request.user.get_full_name() or request.user.username}. "
                        f"Baixe os PDFs dos fornecedores e defina a data de entrega."
                    ),
                    url=f"/sales/orders/",
                )

        messages.success(request, f"Orçamento {quote.number} convertido em pedido. O financeiro definirá a data de entrega.")
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
    if not _is_staff_or_admin(request.user) and quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:quote_list")

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
    # STATIC PAGE IMAGES
    # Place page1 / page2 images in:
    #   config/templates/proposal/
    # This folder is committed to git → works on both local and Railway.
    # To update a page: replace the file, push/redeploy.
    # Supported formats: .jpg  .jpeg  .png  .webp  (first match wins)
    # ════════════════════════════════════════════════════════════
    import os as _os
    from django.conf import settings as _settings
    _PROPOSAL_DIR = _settings.BASE_DIR / 'config' / 'templates' / 'proposal'

    def _draw_static_page(filename):
        """Embed a full-bleed image page; falls back to a blank cream page."""
        drawn = False
        for ext in ('.jpg', '.jpeg', '.png', '.webp'):
            candidate = _PROPOSAL_DIR / (filename + ext)
            if _os.path.isfile(candidate):
                try:
                    c.drawImage(ImageReader(str(candidate)), 0, 0,
                                width=page_w, height=page_h,
                                preserveAspectRatio=False, mask='auto')
                    drawn = True
                except Exception:
                    pass
                break
        if not drawn:
            c.setFillColor(LINEN)
            c.rect(0, 0, page_w, page_h, fill=1, stroke=0)
        c.showPage()

    # ════════════════════════════════════════════════════════════
    # PAGE 1 – COVER  (config/templates/proposal/page1.png)
    # ════════════════════════════════════════════════════════════
    _draw_static_page('page1')

    # ════════════════════════════════════════════════════════════
    # PAGE 2 – SOBRE NÓS  (config/templates/proposal/page2.png)
    # ════════════════════════════════════════════════════════════
    _draw_static_page('page2')

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
            c.drawString(MX, ty, "Valor normal do investimento")
            c.setFont("Helvetica-Bold", 11)
            c.drawRightString(MX + CW, ty, _fmt_brl(subtotal))
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

        # Grand total line shown as discounted value when discount exists.
        c.setFillColor(NAVY)
        c.setFont("Helvetica", 10)
        c.drawString(
            MX,
            ty,
            "Valor do investimento com desconto:" if disc_pct > 0 else "Valor do investimento:",
        )
        c.setFont("Helvetica-Bold", 14)
        c.drawRightString(MX + CW, ty, _fmt_brl(avista if disc_pct > 0 else subtotal))

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
    """
    Gera um PDF de pedido por fornecedor a partir do orçamento.
    - Fornecedor único  → retorna o PDF diretamente.
    - Múltiplos         → retorna ZIP com um PDF por fornecedor.
    - Apenas o NOME do cliente é incluído (sem telefone/e-mail).
    """
    import zipfile as zipfile_mod

    quote = get_object_or_404(
        Quote.objects.select_related("customer", "seller")
                     .prefetch_related("items", "items__supplier"),
        id=quote_id,
    )
    if not _is_staff_or_admin(request.user) and quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:quote_list")
    if not _is_admin(request.user):
        messages.error(request, "Apenas administradores podem baixar o PDF de fornecedor.")
        return redirect("sales:quote_detail", quote_id=quote.id)

    # ── helpers ───────────────────────────────────────────────
    def _fmt_brl(value) -> str:
        s = f"{float(value):,.2f}"
        return s.replace(',', '\x00').replace('.', ',').replace('\x00', '.')

    NAVY  = colors.HexColor('#0A2640')
    LGRAY = colors.HexColor('#DDDDDD')
    BGROW = colors.HexColor('#F8F9FA')
    MUTED = colors.HexColor('#888888')
    _styles = getSampleStyleSheet()

    def _ps(name, **kw):
        return ParagraphStyle(name, parent=_styles['Normal'], **kw)

    def _make_pdf_for_supplier(supplier, items_for_supplier) -> bytes:
        """Gera o binário de um PDF de pedido para um fornecedor específico."""
        st_title   = _ps(f'{supplier.id}_title',  fontSize=15, fontName='Helvetica-Bold',
                         textColor=NAVY, alignment=TA_CENTER, spaceAfter=2)
        st_sub     = _ps(f'{supplier.id}_sub',    fontSize=9, textColor=MUTED,
                         alignment=TA_CENTER, spaceAfter=0)
        st_section = _ps(f'{supplier.id}_sec',    fontSize=9, fontName='Helvetica-Bold',
                         textColor=NAVY, spaceBefore=8, spaceAfter=4)
        st_normal  = _ps(f'{supplier.id}_normal', fontSize=9, leading=13)
        st_label   = _ps(f'{supplier.id}_label',  fontSize=7, textColor=MUTED, leading=11)
        st_footer  = _ps(f'{supplier.id}_footer', fontSize=7, textColor=MUTED, alignment=TA_CENTER)
        st_th      = _ps(f'{supplier.id}_th',     fontSize=8, fontName='Helvetica-Bold',
                         textColor=colors.white, alignment=TA_CENTER)
        st_td_c    = _ps(f'{supplier.id}_td_c',   fontSize=8, alignment=TA_CENTER)
        st_td_l    = _ps(f'{supplier.id}_td_l',   fontSize=8)
        st_td_g    = _ps(f'{supplier.id}_td_g',   fontSize=7, textColor=colors.HexColor('#666666'))

        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            rightMargin=2*cm, leftMargin=2*cm,
            topMargin=2*cm, bottomMargin=2*cm,
        )
        els = []

        # cabeçalho
        els.append(Paragraph("ROSELAR MÓVEIS", st_title))
        els.append(Paragraph("PEDIDO DE COMPRA", st_sub))
        els.append(Spacer(1, 0.3*cm))
        els.append(HRFlowable(width="100%", thickness=2, color=NAVY))
        els.append(Spacer(1, 0.4*cm))

        # metadados (2 colunas)
        seller_name = quote.seller.get_full_name() or quote.seller.username
        meta_data = [
            [
                Paragraph(f"<b>Orçamento:</b> #{quote.number}", st_normal),
                Paragraph(f"<b>Data:</b> {quote.quote_date.strftime('%d/%m/%Y')}", st_normal),
            ],
            [
                Paragraph(f"<b>Vendedor:</b> {seller_name}", st_normal),
                Paragraph("", st_normal),
            ],
        ]
        meta_tbl = Table(meta_data, colWidths=[8.5*cm, 8.5*cm])
        meta_tbl.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',   (0, 0), (-1, -1), 0),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        els.append(meta_tbl)
        els.append(Spacer(1, 0.4*cm))

        # fornecedor / cliente lado a lado
        supplier_cell = [
            Paragraph("<b>Fornecedor</b>", st_section),
            Paragraph(supplier.name, st_normal),
        ]
        if supplier.phone:
            supplier_cell.append(Paragraph(f"Tel: {supplier.phone}", st_label))
        if supplier.email:
            supplier_cell.append(Paragraph(supplier.email, st_label))

        # APENAS o nome do cliente — sem telefone, e-mail ou endereço
        client_cell = [
            Paragraph("<b>Cliente</b>", st_section),
            Paragraph(quote.customer.name, st_normal),
        ]

        party_tbl = Table([[supplier_cell, client_cell]], colWidths=[8.5*cm, 8.5*cm])
        party_tbl.setStyle(TableStyle([
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
            ('BOX',           (0, 0), (0, 0),   0.5, LGRAY),
            ('BOX',           (1, 0), (1, 0),   0.5, LGRAY),
            ('BACKGROUND',    (0, 0), (0, 0),   BGROW),
            ('BACKGROUND',    (1, 0), (1, 0),   BGROW),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('TOPPADDING',    (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        els.append(party_tbl)
        els.append(Spacer(1, 0.5*cm))

        # tabela de itens (sem coluna Fornecedor — o PDF já é do fornecedor)
        els.append(Paragraph("ITENS DO PEDIDO", st_section))
        hdr = [
            Paragraph('#',          st_th),
            Paragraph('Produto',    st_th),
            Paragraph('Descrição',  st_th),
            Paragraph('Qtd',        st_th),
            Paragraph('Vlr. Unit.', st_th),
            Paragraph('Total',      st_th),
        ]
        tdata = [hdr]
        subtotal = Decimal('0.00')
        for idx, item in enumerate(items_for_supplier, 1):
            unit = item.unit_value or Decimal('0.00')
            line = unit * item.quantity
            subtotal += line
            desc = item.description.strip() if item.description else '—'
            tdata.append([
                Paragraph(str(idx),              st_td_c),
                Paragraph(item.product_name,     st_td_l),
                Paragraph(desc,                  st_td_g),
                Paragraph(str(item.quantity),    st_td_c),
                Paragraph(f"R$ {_fmt_brl(unit)}", st_td_c),
                Paragraph(f"R$ {_fmt_brl(line)}", st_td_c),
            ])

        col_w = [0.8*cm, 4.5*cm, 6.5*cm, 1.2*cm, 2.5*cm, 2.5*cm]
        itbl = Table(tdata, colWidths=col_w, repeatRows=1)
        row_styles = [
            ('BACKGROUND',    (0, 0), (-1, 0),  NAVY),
            ('TEXTCOLOR',     (0, 0), (-1, 0),  colors.white),
            ('FONTNAME',      (0, 0), (-1, 0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0, 0), (-1, 0),  8),
            ('TOPPADDING',    (0, 0), (-1, 0),  7),
            ('BOTTOMPADDING', (0, 0), (-1, 0),  7),
            ('GRID',          (0, 0), (-1, -1), 0.5, LGRAY),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 1), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
            ('LEFTPADDING',   (0, 1), (-1, -1), 5),
            ('RIGHTPADDING',  (0, 1), (-1, -1), 5),
        ]
        for i in range(1, len(tdata)):
            if i % 2 == 0:
                row_styles.append(('BACKGROUND', (0, i), (-1, i), BGROW))
        itbl.setStyle(TableStyle(row_styles))
        els.append(itbl)
        els.append(Spacer(1, 0.3*cm))

        # total / frete
        total_tbl = Table(
            [[Paragraph(f"<b>Subtotal:</b> R$ {_fmt_brl(subtotal)}", st_normal)]],
            colWidths=[17*cm],
        )
        total_tbl.setStyle(TableStyle([
            ('ALIGN',         (0, 0), (-1, -1), 'RIGHT'),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ]))
        els.append(total_tbl)

        # prazo
        if quote.delivery_days_min or quote.delivery_days_max:
            mn = quote.delivery_days_min
            mx = quote.delivery_days_max
            if mn and mx:
                prazo_txt = f"{mn} a {mx} dias"
            elif mn:
                prazo_txt = f"a partir de {mn} dias"
            else:
                prazo_txt = f"até {mx} dias"
            els.append(Spacer(1, 0.2*cm))
            els.append(Paragraph(
                f"<b>Prazo de entrega estimado:</b> {prazo_txt}",
                st_normal,
            ))

        # rodapé
        els.append(Spacer(1, 0.8*cm))
        els.append(HRFlowable(width="100%", thickness=0.5, color=LGRAY))
        els.append(Spacer(1, 0.2*cm))
        els.append(Paragraph(
            f"Gerado em {timezone.localdate().strftime('%d/%m/%Y')} | Roselar Móveis",
            st_footer,
        ))

        doc.build(els)
        data = buf.getvalue()
        buf.close()
        return data

    # ── agrupar itens por fornecedor ──────────────────────────
    by_supplier: dict = defaultdict(list)
    items_without_supplier = []
    for item in quote.items.all():
        if item.supplier_id:
            by_supplier[item.supplier_id].append(item)
        else:
            items_without_supplier.append(item)

    if not by_supplier:
        messages.error(request, "Nenhum item com fornecedor cadastrado neste orçamento.")
        return redirect("sales:quote_detail", quote_id=quote.id)

    # avisa itens sem fornecedor mas não bloqueia
    if items_without_supplier:
        nomes = ", ".join(it.product_name for it in items_without_supplier[:5])
        messages.warning(
            request,
            f"Os seguintes itens não têm fornecedor e foram ignorados: {nomes}.",
        )

    # ── um único fornecedor → PDF direto ──────────────────────
    if len(by_supplier) == 1:
        supplier_id, items_list = next(iter(by_supplier.items()))
        supplier = items_list[0].supplier
        pdf_bytes = _make_pdf_for_supplier(supplier, items_list)
        safe_name = supplier.name.replace(' ', '_').replace('/', '_')
        filename = f"pedido_{quote.number}_{safe_name}.pdf"
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = _safe_content_disposition(filename)
        return response

    # ── múltiplos fornecedores → ZIP ──────────────────────────
    zip_buffer = BytesIO()
    with zipfile_mod.ZipFile(zip_buffer, 'w', zipfile_mod.ZIP_DEFLATED) as zf:
        for supplier_id, items_list in by_supplier.items():
            supplier = items_list[0].supplier
            pdf_bytes = _make_pdf_for_supplier(supplier, items_list)
            safe_name = supplier.name.replace(' ', '_').replace('/', '_')
            zf.writestr(f"pedido_{quote.number}_{safe_name}.pdf", pdf_bytes)

    zip_data = zip_buffer.getvalue()
    zip_buffer.close()

    zip_filename = f"pedidos_{quote.number}.zip"
    response = HttpResponse(zip_data, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
    return response


@login_required
def order_list(request: HttpRequest) -> HttpResponse:
    """List all orders with search functionality."""
    orders = Order.objects.select_related('quote', 'supplier', 'quote__customer', 'quote__seller').order_by('-created_at')
    if not _can_view_all_orders(request.user):
        orders = orders.filter(quote__seller=request.user)
    
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
        'is_seller': _is_seller(request.user),
        'can_generate_order_pdf': _can_generate_order_pdf(request.user),
    }
    
    return render(request, 'sales/order_list.html', context)


@login_required
def order_detail(request: HttpRequest, order_id: int) -> HttpResponse:
    """Display order details."""
    order = get_object_or_404(
        Order.objects.select_related('quote', 'supplier', 'quote__customer', 'quote__seller'),
        pk=order_id
    )
    if not _can_view_all_orders(request.user) and order.quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:order_list")
    
    items = order.items.select_related('quote_item').all()
    
    # Calculate total
    total = sum(item.line_total for item in items)
    
    context = {
        'order': order,
        'items': items,
        'total': total,
        'is_seller': _is_seller(request.user),
        'can_generate_order_pdf': _can_generate_order_pdf(request.user),
        'can_set_delivery': _can_generate_order_pdf(request.user),  # financeiro/admin
        # lista pedidos por fornecedor do mesmo orçamento (para o financeiro baixar PDFs)
        'supplier_orders': (
            order.quote.orders.select_related('supplier')
                               .filter(is_total_conference=False)
                               .order_by('supplier__name')
            if order.is_total_conference else None
        ),
    }

    return render(request, 'sales/order_detail.html', context)


@login_required
@require_http_methods(["POST"])
def order_set_delivery(request: HttpRequest, order_id: int) -> HttpResponse:
    """
    Financeiro define a data de entrega estimada do pedido.
    Só pode ser chamado sobre o pedido total (is_total_conference=True).
    Propaga a data para todos os pedidos por fornecedor do mesmo orçamento
    e cria lembretes automáticos para o vendedor e para o financeiro.
    """
    order = get_object_or_404(
        Order.objects.select_related('quote', 'quote__customer', 'quote__seller'),
        pk=order_id,
    )
    if not _can_generate_order_pdf(request.user):
        messages.error(request, "Acesso negado.")
        return redirect("sales:order_list")

    if not order.is_total_conference:
        messages.error(request, "A data de entrega deve ser definida no pedido total.")
        return redirect("sales:order_detail", order_id=order.id)

    delivery_str = request.POST.get("delivery_deadline", "").strip()
    try:
        delivery_date = date_type.fromisoformat(delivery_str) if delivery_str else None
    except ValueError:
        delivery_date = None

    if not delivery_date:
        messages.error(request, "Data de entrega inválida.")
        return redirect("sales:order_detail", order_id=order.id)

    quote = order.quote
    seller_name = quote.seller.get_full_name() or quote.seller.username
    customer_name = quote.customer.name

    # Conta os itens para o resumo
    item_count = order.items.count()
    subtotal = sum(it.line_total for it in order.items.all())
    subtotal_fmt = f"R$ {float(subtotal):,.2f}".replace(',', '\x00').replace('.', ',').replace('\x00', '.')

    # Propaga a data para todos os pedidos do orçamento
    Order.objects.filter(quote=quote).update(delivery_deadline=delivery_date)

    # Cria lembretes de entrega no calendário para o vendedor e para o financeiro
    from core.models import AuditLog, AuditAction, Notification, NotificationType

    reminder_title = f"Entrega prevista — {quote.number} | {customer_name}"
    reminder_description = (
        f"Pedido {quote.number}\n"
        f"Cliente: {customer_name}\n"
        f"Vendedor: {seller_name}\n"
        f"Itens: {item_count} | Total: {subtotal_fmt}\n"
        f"Data de entrega definida por: {request.user.get_full_name() or request.user.username}"
    )

    recipients = set()
    recipients.add(quote.seller)    # sempre o vendedor que fechou a venda
    recipients.add(request.user)    # o financeiro que está definindo a data

    for recipient in recipients:
        event, _ = CalendarEvent.objects.get_or_create(
            quote=quote,
            order=order,
            event_type=EventType.DELIVERY,
            assigned_to=recipient,
            defaults={
                "title": reminder_title,
                "description": reminder_description,
                "status": EventStatus.PENDING,
                "event_date": delivery_date,
                "customer": quote.customer,
            },
        )
        # Atualiza data se o evento já existia (caso re-definição)
        if event.event_date != delivery_date:
            event.event_date = delivery_date
            event.description = reminder_description
            event.save(update_fields=["event_date", "description"])

        Reminder.objects.get_or_create(
            event=event,
            defaults={
                "remind_date": delivery_date,
                "message": reminder_title,
            },
        )

        Notification.send(
            recipient,
            f"Entrega agendada: {quote.number}",
            NotificationType.DELIVERY_NEAR,
            message=(
                f"Data de entrega definida para {delivery_date.strftime('%d/%m/%Y')}.\n"
                f"Cliente: {customer_name} | {item_count} itens | {subtotal_fmt}"
            ),
            url=f"/sales/orders/{order.id}/",
        )

    AuditLog.log(
        request.user,
        AuditAction.CONVERT_ORDER,
        f"Data de entrega definida para {quote.number}: {delivery_date.strftime('%d/%m/%Y')}",
        obj=quote,
        ip_address=request.META.get('REMOTE_ADDR'),
    )

    messages.success(
        request,
        f"Data de entrega definida: {delivery_date.strftime('%d/%m/%Y')}. "
        f"Lembretes criados para {quote.seller.get_full_name() or quote.seller.username} e para você."
    )
    return redirect("sales:order_detail", order_id=order.id)


@login_required
def order_pdf(request: HttpRequest, order_id: int) -> HttpResponse:
    """Generate PDF for a specific supplier purchase order (one PDF per supplier)."""
    order = get_object_or_404(
        Order.objects.select_related('quote', 'supplier', 'quote__customer', 'quote__seller'),
        pk=order_id
    )
    if not _can_generate_order_pdf(request.user):
        messages.error(request, "Seu perfil não pode baixar PDFs de pedidos.")
        return redirect("sales:order_list")
    if not _can_view_all_orders(request.user) and order.quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:order_list")

    if order.is_total_conference:
        messages.error(request, "Não é possível gerar PDF para pedido de conferência total.")
        return redirect('sales:order_detail', order_id=order.id)

    def _fmt_brl(value) -> str:
        """Formata número no padrão brasileiro: 1.234,56"""
        s = f"{float(value):,.2f}"
        return s.replace(',', '\x00').replace('.', ',').replace('\x00', '.')

    NAVY   = colors.HexColor('#0A2640')
    LGRAY  = colors.HexColor('#DDDDDD')
    BGROW  = colors.HexColor('#F8F9FA')
    MUTED  = colors.HexColor('#888888')

    styles = getSampleStyleSheet()

    def _ps(name, **kw):
        return ParagraphStyle(name, parent=styles['Normal'], **kw)

    st_title    = _ps('od_title',   fontSize=15, fontName='Helvetica-Bold',
                      textColor=NAVY, alignment=TA_CENTER, spaceAfter=2)
    st_sub      = _ps('od_sub',     fontSize=9,  textColor=MUTED,
                      alignment=TA_CENTER, spaceAfter=0)
    st_section  = _ps('od_sec',     fontSize=9,  fontName='Helvetica-Bold',
                      textColor=NAVY, spaceBefore=8, spaceAfter=4)
    st_normal   = _ps('od_normal',  fontSize=9,  leading=13)
    st_label    = _ps('od_label',   fontSize=7,  textColor=MUTED, leading=11)
    st_footer   = _ps('od_footer',  fontSize=7,  textColor=MUTED, alignment=TA_CENTER)
    st_th       = _ps('od_th',      fontSize=8,  fontName='Helvetica-Bold',
                      textColor=colors.white, alignment=TA_CENTER)
    st_td_c     = _ps('od_td_c',    fontSize=8,  alignment=TA_CENTER)
    st_td_l     = _ps('od_td_l',    fontSize=8)
    st_td_g     = _ps('od_td_g',    fontSize=7,  textColor=colors.HexColor('#666666'))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )
    elements = []

    # ── Cabeçalho ──────────────────────────────────────────────
    elements.append(Paragraph("ROSELAR MÓVEIS", st_title))
    elements.append(Paragraph("PEDIDO DE COMPRA", st_sub))
    elements.append(Spacer(1, 0.3*cm))
    elements.append(HRFlowable(width="100%", thickness=2, color=NAVY))
    elements.append(Spacer(1, 0.4*cm))

    # ── Dados do pedido (2 colunas) ────────────────────────────
    seller_name = order.quote.seller.get_full_name() or order.quote.seller.username
    prazo = order.delivery_deadline.strftime('%d/%m/%Y') if order.delivery_deadline else '—'
    meta_data = [
        [
            Paragraph(f"<b>Pedido:</b> #{order.number}", st_normal),
            Paragraph(f"<b>Data:</b> {order.created_at.strftime('%d/%m/%Y')}", st_normal),
        ],
        [
            Paragraph(f"<b>Vendedor:</b> {seller_name}", st_normal),
            Paragraph(f"<b>Prazo de entrega:</b> {prazo}", st_normal),
        ],
    ]
    meta_tbl = Table(meta_data, colWidths=[8.5*cm, 8.5*cm])
    meta_tbl.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_tbl)
    elements.append(Spacer(1, 0.4*cm))

    # ── Fornecedor / Cliente (2 colunas) ───────────────────────
    # Apenas o NOME do cliente vai no pedido (sem telefone, email, endereço).
    supplier_cell = []
    if order.supplier:
        supplier_cell.append(Paragraph(f"<b>Fornecedor</b>", st_section))
        supplier_cell.append(Paragraph(order.supplier.name, st_normal))
        if order.supplier.phone:
            supplier_cell.append(Paragraph(f"Tel: {order.supplier.phone}", st_label))
        if order.supplier.email:
            supplier_cell.append(Paragraph(order.supplier.email, st_label))
    else:
        supplier_cell.append(Paragraph("—", st_normal))

    client_cell = [
        Paragraph("<b>Cliente</b>", st_section),
        Paragraph(order.quote.customer.name, st_normal),
    ]

    party_tbl = Table([[supplier_cell, client_cell]], colWidths=[8.5*cm, 8.5*cm])
    party_tbl.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('BOX',           (0, 0), (0, 0),   0.5, LGRAY),
        ('BOX',           (1, 0), (1, 0),   0.5, LGRAY),
        ('BACKGROUND',    (0, 0), (0, 0),   BGROW),
        ('BACKGROUND',    (1, 0), (1, 0),   BGROW),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('TOPPADDING',    (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(party_tbl)
    elements.append(Spacer(1, 0.5*cm))

    # ── Tabela de itens ────────────────────────────────────────
    elements.append(Paragraph("ITENS DO PEDIDO", st_section))

    items_qs = list(order.items.all())

    hdr = [
        Paragraph('#',          st_th),
        Paragraph('Produto',    st_th),
        Paragraph('Descrição',  st_th),
        Paragraph('Qtd',        st_th),
        Paragraph('Vlr. Unit.', st_th),
        Paragraph('Total',      st_th),
    ]
    table_data = [hdr]

    for idx, item in enumerate(items_qs, 1):
        desc = item.description.strip() if item.description else '—'
        unit = item.purchase_unit_cost or Decimal('0.00')
        total_item = unit * item.quantity
        table_data.append([
            Paragraph(str(idx),                    st_td_c),
            Paragraph(item.product_name,           st_td_l),
            Paragraph(desc,                        st_td_g),
            Paragraph(str(item.quantity),          st_td_c),
            Paragraph(f"R$ {_fmt_brl(unit)}",      st_td_c),
            Paragraph(f"R$ {_fmt_brl(total_item)}", st_td_c),
        ])

    col_widths = [0.8*cm, 4.5*cm, 6.5*cm, 1.2*cm, 2.5*cm, 2.5*cm]
    items_tbl = Table(table_data, colWidths=col_widths, repeatRows=1)

    row_styles = [
        ('BACKGROUND',    (0, 0), (-1, 0),  NAVY),
        ('TEXTCOLOR',     (0, 0), (-1, 0),  colors.white),
        ('FONTNAME',      (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0, 0), (-1, 0),  8),
        ('TOPPADDING',    (0, 0), (-1, 0),  7),
        ('BOTTOMPADDING', (0, 0), (-1, 0),  7),
        ('GRID',          (0, 0), (-1, -1), 0.5, LGRAY),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 1), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('LEFTPADDING',   (0, 1), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 1), (-1, -1), 5),
    ]
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            row_styles.append(('BACKGROUND', (0, i), (-1, i), BGROW))

    items_tbl.setStyle(TableStyle(row_styles))
    elements.append(items_tbl)
    elements.append(Spacer(1, 0.3*cm))

    # ── Total ──────────────────────────────────────────────────
    grand_total = sum((it.purchase_unit_cost or Decimal('0.00')) * it.quantity for it in items_qs)
    total_tbl = Table(
        [[Paragraph(f"<b>Total do pedido:</b> R$ {_fmt_brl(grand_total)}", st_normal)]],
        colWidths=[17*cm],
    )
    total_tbl.setStyle(TableStyle([
        ('ALIGN',         (0, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
    ]))
    elements.append(total_tbl)

    # ── Observações
    if order.notes:
        elements.append(Spacer(1, 0.4*cm))
        elements.append(Paragraph("OBSERVAÇÕES", st_section))
        elements.append(Paragraph(order.notes, st_normal))

    # ── Rodapé
    elements.append(Spacer(1, 0.8*cm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=LGRAY))
    elements.append(Spacer(1, 0.2*cm))
    elements.append(Paragraph(
        f"Gerado em {timezone.localdate().strftime('%d/%m/%Y')} | Roselar Móveis",
        st_footer,
    ))

    # ── Gerar PDF
    try:
        doc.build(elements)
    except Exception:
        logger.exception('Erro ao gerar PDF do pedido %s', order.number)
        raise

    pdf = buffer.getvalue()
    buffer.close()

    supplier_name = order.supplier.name if order.supplier else 'sem_fornecedor'
    filename = f'pedido_{order.number}_{supplier_name}.pdf'
    filename = filename.replace(' ', '_').replace('/', '_')
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = _safe_content_disposition(filename)
    return response


# ──────────────────────────────────────────────────────────────────────
# Shared simulation logic
# ──────────────────────────────────────────────────────────────────────
def _reverse_calc_from_target(
    target_final: Decimal,
    subtotal: Decimal,
    freight_value: Decimal,
    client_surcharge_percent: Decimal,
) -> tuple[Decimal, Decimal]:
    """Given a desired final total, derive discount% and price_increase%.

    Strategy:
    1. Try discount (with price_increase=0). If discount >= 0, use it.
    2. If discount < 0 (target > base total), use discount=0 and solve for
       price_increase instead.

    Returns (discount_percent, price_increase_percent).
    """
    surcharge_mult = (Decimal("100") + client_surcharge_percent) / Decimal("100")
    base_total_before_disc = subtotal + freight_value

    if base_total_before_disc <= 0 or target_final <= 0:
        return Decimal("0"), Decimal("0")

    # Case 1: solve for discount with pi=0
    # target = base_total_before_disc × (1 - d/100) × surcharge_mult
    # d = 100 × (1 - target / (surcharge_mult × base_total_before_disc))
    discount = Decimal("100") * (
        Decimal("1") - target_final / (surcharge_mult * base_total_before_disc)
    )

    if discount >= 0:
        # Target is at or below base → use discount, no price increase
        return max(Decimal("0"), discount), Decimal("0")

    # Case 2: target > base total → need price increase, no discount
    # target = (subtotal × (1 + pi/100) + freight) × surcharge_mult
    # subtotal × (1 + pi/100) = target / surcharge_mult - freight
    # pi = 100 × ((target / surcharge_mult - freight) / subtotal - 1)
    if subtotal <= 0:
        return Decimal("0"), Decimal("0")

    pi = Decimal("100") * (
        (target_final / surcharge_mult - freight_value) / subtotal - Decimal("1")
    )
    return Decimal("0"), max(Decimal("0"), pi)


def _build_simulation_context(
    *,
    subtotal: Decimal,
    freight_value: Decimal,
    sim_payment_type: str,
    sim_has_architect: bool,
    sim_discount: Decimal,
    price_increase_pct: Decimal,
    sim_installments: int,
    target_final: Decimal | None = None,
    target_installment: Decimal | None = None,
    # split payment
    sim_payment_type_2: str = '',
    sim_installments_2: int = 1,
    sim_split_amount: Decimal | None = None,
    price_increase_pct_2: Decimal = Decimal("0"),
    # down payment
    down_payment_value: Decimal | None = None,
) -> dict:
    """Pure calculation — no DB writes, no request handling."""
    from core.models import PaymentTariff, PaymentMethodType, ArchitectCommission, SalesMarginConfig

    _tm, _mc_min, _mc_max = SalesMarginConfig.get_config()
    MARGIN_BASE = Decimal(str(_tm))
    margin_limit = MARGIN_BASE

    COMMISSION_FLOOR = Decimal(str(_mc_min))
    COMMISSION_MAX   = Decimal(str(_mc_max))
    MAX_DISCOUNT_ABSOLUTE = Decimal("30")

    price_increase_pct   = max(Decimal('0'), min(price_increase_pct, Decimal('30')))
    price_increase_pct_2 = max(Decimal('0'), min(price_increase_pct_2, Decimal('30')))
    sim_discount         = max(Decimal("0"), min(sim_discount, MAX_DISCOUNT_ABSOLUTE))
    sim_installments     = max(1, min(sim_installments, 18))
    sim_installments_2   = max(1, min(sim_installments_2, 18))

    # À vista payment types: fixed commission, no fee cost to store margin
    _AVISTA_TYPES = {'PIX', 'CASH', 'DEBIT_CARD', 'CHEQUE', 'BOLETO'}

    # ── Payment method 1 fees ───────────────────────────────────────────
    payment_fee_percent = (
        Decimal(str(PaymentTariff.get_fee(sim_payment_type, sim_installments)))
        if sim_payment_type else Decimal("0")
    )
    # Loja absorve a taxa integral em todos os prazos — sem repasse ao cliente
    store_fee_percent = payment_fee_percent
    client_surcharge_percent = Decimal("0")

    # ── Payment method 2 fees (split mode) ─────────────────────────────
    split_mode = bool(sim_payment_type_2)
    payment_fee_percent_2 = Decimal("0")
    store_fee_percent_2   = Decimal("0")
    client_surcharge_percent_2 = Decimal("0")
    if split_mode:
        payment_fee_percent_2 = (
            Decimal(str(PaymentTariff.get_fee(sim_payment_type_2, sim_installments_2)))
            if sim_payment_type_2 else Decimal("0")
        )
        store_fee_percent_2 = payment_fee_percent_2

    # ── Down payment: effective fee for margin/commission ───────────────
    # Pre-compute gross total (uses current price_increase_pct and sim_discount;
    # target_final may later refine these, but we use this estimate for commissions)
    _dp_input = max(Decimal("0"), down_payment_value or Decimal("0"))
    _dp_pre_total = max(
        Decimal("0"),
        subtotal * (Decimal("100") + price_increase_pct) / Decimal("100")
        + freight_value
        - (subtotal + freight_value) * sim_discount / Decimal("100"),
    )
    _dp_capped = min(_dp_input, _dp_pre_total)
    # Enforce minimum down payment = 1 installment (early estimate for commission calc)
    if down_payment_value is not None and sim_installments > 1 and _dp_pre_total > 0:
        _dp_min_est = (_dp_pre_total / Decimal(sim_installments + 1)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        _dp_capped = max(_dp_min_est, _dp_capped)
    _dp_fin_ratio = (
        (_dp_pre_total - _dp_capped) / _dp_pre_total
        if _dp_pre_total > 0 else Decimal("1")
    )
    # Effective fee: only the financed portion bears the card fee
    eff_store_fee_percent = (store_fee_percent * _dp_fin_ratio).quantize(Decimal("0.000001"))
    # Down payment ratio (used for commission base later)
    _dp_down_ratio = Decimal("1") - _dp_fin_ratio

    # ── Early avista detection (needed before target_final reverse calc) ──
    _split_m1_avista_pre = split_mode and sim_payment_type in _AVISTA_TYPES
    _split_m2_avista_pre = split_mode and sim_payment_type_2 in _AVISTA_TYPES

    # Parcela desejada: converte em target_final
    target_installment_mode = False
    if target_installment is not None and target_installment > 0 and sim_installments > 1 and subtotal > 0:
        target_final = target_installment * Decimal(sim_installments)
        target_installment_mode = True

    # Valor final desejado: calcula desconto/ajuste reverso
    target_mode = False
    if target_final is not None and target_final > 0 and subtotal > 0:
        if split_mode and _split_m1_avista_pre and sim_split_amount is not None:
            # Method 1 is PIX/cash (0% fee) — solve for pi2 on card portion or discount.
            # pi1 must stay 0 (inflating adj_subtotal doesn't help for à-vista split).
            base_total_pre = subtotal + freight_value
            disc_val_pre = base_total_pre * sim_discount / Decimal("100")
            total_base_pre = max(Decimal("0"), base_total_pre - disc_val_pre)
            s1_pre = max(Decimal("0"), min(sim_split_amount, total_base_pre))
            s2_pre = max(Decimal("0"), total_base_pre - s1_pre)
            cs2_mult_pre = (Decimal("100") + client_surcharge_percent_2) / Decimal("100")
            base_final_pre = s1_pre + s2_pre * cs2_mult_pre  # total at pi2=0
            if s2_pre > 0 and target_final > base_final_pre:
                # Need price increase on card portion
                pi2_mult = (target_final - s1_pre) / (s2_pre * cs2_mult_pre)
                price_increase_pct_2 = min(
                    max(Decimal("0"), (pi2_mult - Decimal("1")) * Decimal("100")),
                    Decimal("30"),
                ).quantize(Decimal("0.1"))
            elif target_final < base_final_pre and base_total_pre > 0:
                # Need discount — reduce card split portion
                new_s2_needed = max(Decimal("0"), (target_final - s1_pre) / cs2_mult_pre)
                total_needed = s1_pre + new_s2_needed
                new_d = max(Decimal("0"), min(
                    Decimal("100") * (Decimal("1") - total_needed / base_total_pre),
                    MAX_DISCOUNT_ABSOLUTE,
                ))
                sim_discount = new_d.quantize(Decimal("0.1"))
                price_increase_pct_2 = Decimal("0")
            price_increase_pct = Decimal("0")  # never inflate PIX portion
            target_mode = True
        elif split_mode and not _split_m1_avista_pre and not _split_m2_avista_pre:
            # Both methods are card — apply same pi to both (blended approach)
            sim_discount, price_increase_pct = _reverse_calc_from_target(
                target_final, subtotal, freight_value, client_surcharge_percent,
            )
            price_increase_pct = min(price_increase_pct, Decimal("30")).quantize(Decimal("0.1"))
            sim_discount = min(sim_discount, MAX_DISCOUNT_ABSOLUTE).quantize(Decimal("0.1"))
            price_increase_pct_2 = price_increase_pct  # mirror to card 2
            target_mode = True
        else:
            # Single mode (or mixed split with method 2 à vista)
            sim_discount, price_increase_pct = _reverse_calc_from_target(
                target_final, subtotal, freight_value, client_surcharge_percent,
            )
            price_increase_pct = min(price_increase_pct, Decimal("30")).quantize(Decimal("0.1"))
            sim_discount = min(sim_discount, MAX_DISCOUNT_ABSOLUTE).quantize(Decimal("0.1"))
            target_mode = True

    architect_percent = ArchitectCommission.get_commission()
    architect_commission_value = Decimal("0")
    architect_cost_pct = architect_percent if sim_has_architect else Decimal("0")

    # ── Split weights (computed once; used for both margin and commission) ──
    _split_m1_avista = split_mode and sim_payment_type in _AVISTA_TYPES
    _w1 = Decimal("0.5")
    _w2 = Decimal("0.5")
    if split_mode:
        _pm = (Decimal("100") + price_increase_pct) / Decimal("100")
        _tot_est = subtotal * _pm + freight_value - (subtotal + freight_value) * sim_discount / Decimal("100")
        if _tot_est > 0:
            _raw_s1 = max(Decimal("0"), min(sim_split_amount, _tot_est)) if sim_split_amount is not None else _tot_est / 2
            _w1 = _raw_s1 / _tot_est
        _w2 = Decimal("1") - _w1

    # ── Blended effective margin and fixed costs ────────────────────────
    # In split mode: weight each method's fee and price-increase contribution.
    # À vista method 1 needs no price increase (0% fee), so pi1 = 0 for that case.
    _pi1_eff = Decimal("0") if _split_m1_avista else price_increase_pct
    if split_mode:
        _blended_pi  = _w1 * _pi1_eff + _w2 * price_increase_pct_2
        _blended_fee = _w1 * eff_store_fee_percent + _w2 * store_fee_percent_2
    else:
        _blended_pi  = price_increase_pct
        _blended_fee = eff_store_fee_percent

    # ── Down-payment margin boost ────────────────────────────────────────
    # The down-payment portion is received upfront (no card-fee drag), so the
    # store's effective margin grows proportionally:
    #   new_margin = MARGIN_BASE × (1 + dp_ratio)
    # e.g. MARGIN=10%, DP=33.33% → 10% × 1.3333 = 13.33%
    _dp_margin_boost = (MARGIN_BASE * _dp_down_ratio).quantize(Decimal("0.000001"))

    effective_margin = MARGIN_BASE + _blended_pi + _dp_margin_boost
    max_discount_allowed = MAX_DISCOUNT_ABSOLUTE

    # ── Seller commission — schedule + dynamic 7x+ growth ────────────────
    # Rules:
    #   PIX / Dinheiro / Cheque  → 5 %    (fixed)
    #   Débito                   → 4 %    (fixed)
    #   Crédito ou Boleto 1–6x   → 3 %    (fixed)
    #   Crédito ou Boleto 7x+    → 2 % BASE, GROWS when the seller adds
    #                              price-increase or the client pays upfront.
    #                              Capped at COMMISSION_MAX (admin setting).
    #
    # 7x+ formula uses the available-margin equation (multiplicative fee):
    #     adj_factor    = (100 + pi − discount) / 100
    #     effective_fee = fee_pct × adj_factor   ← fee on adjusted (post-pi) base
    #     available     = MARGIN_BASE + pi + dp_boost
    #                     − effective_fee − discount − architect
    #     commission    = clamp(available, FLOOR=2 %, COMMISSION_MAX)
    #
    # This means each percentage-point of `pi` (or upfront) translates into
    # extra commission (roughly 1−fee/100 percentage points per 1 % pi),
    # giving the seller a real reason to bump the price for 7x+ deals.
    _TYPE_COMMISSION_CAP: dict[str, Decimal] = {
        'PIX':        Decimal('5'),
        'CASH':       Decimal('5'),
        'DEBIT_CARD': Decimal('4'),
        'CHEQUE':     Decimal('5'),
    }

    def _available_for_commission(fee_pct: Decimal, pi: Decimal) -> Decimal:
        """Margin remaining after fee/discount/architect (the 7x+ commission pool).

        NOTE: The down-payment is intentionally treated as **neutral** here —
        we ignore both the margin boost AND the fee reduction it produces.
        Rationale: the seller's commission should reflect the deal's quality
        (price increase, discount, architect, payment method), not how the
        client chose to fund it. The store still benefits from upfront cash
        in the actual margin status check below; this helper is only for
        the dynamic 7x+ commission ladder."""
        adj_factor    = max(Decimal('0'), (Decimal('100') + pi - sim_discount) / Decimal('100'))
        effective_fee = fee_pct * adj_factor
        return (
            MARGIN_BASE + pi
            - effective_fee
            - architect_cost_pct
            - sim_discount
        )

    def _get_comm(payment_type: str, installments: int, fee_pct: Decimal, pi: Decimal) -> Decimal:
        """Commission per the schedule. Fixed for instant types and ≤6x cards;
        dynamic (2 %→MAX) for 7x+ credit/boleto so seller benefits from pi/upfront."""
        rate = _TYPE_COMMISSION_CAP.get(payment_type)
        if rate is not None:
            return rate
        # Credit / Boleto
        if installments <= 6:
            return Decimal('3')
        # 7x+: dynamic — starts at 2 %, grows with pi/upfront, capped at MAX
        avail = _available_for_commission(fee_pct, pi)
        return max(COMMISSION_FLOOR, min(avail, COMMISSION_MAX))

    # ── Helper: minimum price-increase delta to reach a commission target ──────
    # Solves the correct (multiplicative) equation:
    #   available(pi_total) = target_comm
    #   → pi_total = (target_comm − M − dp + fee×(1−disc/100) + disc + arch) / (1 − fee/100)
    #   → delta    = max(0, pi_total − current_pi)
    # `for_commission=True` makes the down-payment neutral (no dp boost,
    # raw fee) — used by the 7x+ "earn more" incentive so the suggestion
    # matches the commission formula in `_available_for_commission`.
    def _pi_needed(target_comm: Decimal, fee: Decimal, current_pi: Decimal,
                   *, for_commission: bool = False) -> Decimal:
        denom = Decimal('1') - fee / Decimal('100')
        if denom <= Decimal('0.001'):          # fee ≥ 100 % — mathematically impossible
            return Decimal('100')
        dp_term = Decimal('0') if for_commission else _dp_margin_boost
        pi_total = (
            target_comm
            - MARGIN_BASE
            - dp_term
            + fee * (Decimal('1') - sim_discount / Decimal('100'))
            + sim_discount
            + architect_cost_pct
        ) / denom
        return max(Decimal('0'), pi_total - current_pi)

    if split_mode:
        if _split_m1_avista:
            _comm1 = _get_comm(sim_payment_type, sim_installments, Decimal('0'), Decimal('0'))
            _comm2 = _get_comm(sim_payment_type_2, sim_installments_2, store_fee_percent_2, price_increase_pct_2)
            seller_commission_percent = (_w1 * _comm1 + _w2 * _comm2).quantize(Decimal("0.1"))
        else:
            # Commission uses RAW store_fee_percent (not eff_store_fee_percent) —
            # the down-payment must be neutral for commission purposes.
            _comm1 = _get_comm(sim_payment_type, sim_installments, store_fee_percent, price_increase_pct)
            _comm2 = _get_comm(sim_payment_type_2, sim_installments_2, store_fee_percent_2, price_increase_pct_2)
            seller_commission_percent = (_w1 * _comm1 + _w2 * _comm2).quantize(Decimal("0.1"))
    else:
        # Commission uses RAW store_fee_percent — down-payment is neutral for commission.
        seller_commission_percent = _get_comm(sim_payment_type, sim_installments, store_fee_percent, price_increase_pct).quantize(Decimal("0.1"))

    original_commission_percent = COMMISSION_MAX

    # ── Global margin status (blended costs vs blended margin) ──────────
    # Corrected fixed costs: fee is applied to the adjusted (post-pi, post-discount) total.
    # adj_factor = (100 + blended_pi − discount) / 100
    _blended_adj_factor   = max(Decimal('0'), (Decimal('100') + _blended_pi - sim_discount) / Decimal('100'))
    _blended_fee_on_base  = _blended_fee * _blended_adj_factor   # fee as % of base (multiplicative)
    fixed_costs           = _blended_fee_on_base + architect_cost_pct + sim_discount

    # Seller commission is NOT drawn from the gross margin — it is a separate cost.
    # Margin check is based on fixed costs only (fee + discount + architect).
    total_cost_pct     = fixed_costs
    margin_exceeded    = total_cost_pct > effective_margin
    commission_reduced = seller_commission_percent < COMMISSION_MAX
    margin_excess      = total_cost_pct - effective_margin if margin_exceeded else Decimal("0")

    # Hard block: even at 0 % commission the fixed costs exceed the margin limit.
    margin_limit_exceeded = fixed_costs > (margin_limit + _blended_pi + _dp_margin_boost)
    controls_blocked      = margin_limit_exceeded

    # Minimum increase to unblock (break-even, target commission = 0):
    min_increase_to_unblock = Decimal("0")
    if margin_limit_exceeded:
        _delta_unblock = _pi_needed(Decimal('0'), _blended_fee, _blended_pi)
        if _delta_unblock > Decimal("0"):
            min_increase_to_unblock = _delta_unblock.quantize(Decimal("0.1"), rounding=ROUND_CEILING)

    # ── Suggested price increase ─────────────────────────────────────────
    # Two distinct purposes:
    #   (a) margin_exceeded → cost > margin, suggest pi to break even (cover
    #       the fixed-rate commission already promised, e.g. 3 % at 6x with
    #       heavy discount).
    #   (b) 7x+ credit/boleto with commission < COMMISSION_MAX → pure incentive:
    #       suggest pi that would lift the dynamic commission up to MAX.
    suggested_increase = Decimal("0")
    suggestion_is_opportunity = False  # True = "you could earn more" (incentive), False = warning
    if margin_exceeded:
        # Suggest pi to bring fixed costs back within margin (commission not in margin).
        _delta_suggest = _pi_needed(Decimal('0'), _blended_fee, _blended_pi)
        suggested_increase = _delta_suggest.quantize(Decimal("0.1"), rounding=ROUND_CEILING)
    elif (
        not split_mode
        and sim_payment_type in {'CREDIT_CARD', 'BOLETO'}
        and sim_installments >= 7
        and seller_commission_percent < Decimal('3')
    ):
        # 7x+: dynamic commission has room to grow.  Suggest pi to hit 3 % (not max).
        # Uses for_commission=True so the math matches _available_for_commission
        # (down-payment is neutral for commission). Fee is the RAW store_fee_percent.
        _delta_suggest = _pi_needed(Decimal('3'), store_fee_percent, price_increase_pct, for_commission=True)
        if _delta_suggest > Decimal("0"):
            suggested_increase = _delta_suggest.quantize(Decimal("0.1"), rounding=ROUND_CEILING)
            suggestion_is_opportunity = True

    # ── Per-method margin status (shown on individual sliders) ──────────
    _split_m2_avista = split_mode and sim_payment_type_2 in _AVISTA_TYPES
    split_both_cards = split_mode and bool(sim_payment_type_2) and not _split_m1_avista and not _split_m2_avista
    if split_mode:
        _eff1      = MARGIN_BASE + _pi1_eff + _dp_margin_boost
        _eff2      = MARGIN_BASE + price_increase_pct_2 + _dp_margin_boost
        # Corrected per-method fee on base (multiplicative)
        _adj1      = max(Decimal('0'), (Decimal('100') + _pi1_eff - sim_discount) / Decimal('100'))
        _adj2      = max(Decimal('0'), (Decimal('100') + price_increase_pct_2 - sim_discount) / Decimal('100'))
        _c1        = eff_store_fee_percent * _adj1 + architect_cost_pct + sim_discount
        _c2        = store_fee_percent_2   * _adj2 + architect_cost_pct + sim_discount
        margin_exceeded_1    = _c1 > _eff1
        margin_exceeded_2    = _c2 > _eff2
        suggested_increase_1 = (
            _pi_needed(Decimal('0'), eff_store_fee_percent, _pi1_eff).quantize(Decimal("0.1"), rounding=ROUND_CEILING)
            if margin_exceeded_1 else Decimal("0")
        )
        suggested_increase_2 = (
            _pi_needed(Decimal('0'), store_fee_percent_2, price_increase_pct_2).quantize(Decimal("0.1"), rounding=ROUND_CEILING)
            if margin_exceeded_2 else Decimal("0")
        )
    else:
        margin_exceeded_1    = False
        margin_exceeded_2    = False
        suggested_increase_1 = Decimal("0")
        suggested_increase_2 = Decimal("0")

    # True when global conditions are within margin but ≥1 individual card method is over its own
    any_method_over_margin = split_mode and not margin_exceeded and (margin_exceeded_1 or margin_exceeded_2)

    price_multiplier  = (Decimal("100") + price_increase_pct) / Decimal("100")
    adj_subtotal      = subtotal * price_multiplier
    total_before_disc = adj_subtotal + freight_value
    # Desconto sempre sobre valor base (sem ajuste de margem)
    base_total        = subtotal + freight_value
    discount_value    = base_total * sim_discount / Decimal("100")
    total_after_disc  = total_before_disc - discount_value

    # Down payment: cap against real total, enforce minimum = 1 installment
    down_payment_used = min(_dp_capped, total_after_disc)
    if down_payment_value is not None and sim_installments > 1 and total_after_disc > 0:
        dp_min_value = (total_after_disc / Decimal(sim_installments + 1)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        down_payment_used = max(dp_min_value, down_payment_used)
    else:
        dp_min_value = Decimal("0")
    financed_value    = max(Decimal("0"), total_after_disc - down_payment_used)

    # ── Split payment fee calculation ──────────────────────────────────
    # split_1 / split_2 = net base portions of total_after_disc
    # split_amount_1/2  = what the client actually pays for each portion
    #   (includes per-method price increase to recover excess fee)
    if split_mode and total_after_disc > 0:
        # Base net portions
        if sim_split_amount is not None:
            split_1 = max(Decimal("0"), min(sim_split_amount, total_after_disc))
        else:
            split_1 = total_after_disc / 2
        split_2 = total_after_disc - split_1

        # Per-method price-increase multipliers (fee-recovery markup)
        # À vista method 1 has no fee, so no markup needed regardless of slider
        _pi1_mult = (Decimal("100") + price_increase_pct) / Decimal("100") if not _split_m1_avista else Decimal("1")
        _pi2_mult = (Decimal("100") + price_increase_pct_2) / Decimal("100")
        split_amount_1 = split_1 * _pi1_mult
        split_amount_2 = split_2 * _pi2_mult

        # Fees applied to inflated client-facing amounts
        payment_fee_value_1 = split_amount_1 * store_fee_percent / Decimal("100")
        payment_fee_value_2 = split_amount_2 * store_fee_percent_2 / Decimal("100")
        payment_fee_value   = payment_fee_value_1 + payment_fee_value_2

        client_surcharge_value_1 = split_amount_1 * client_surcharge_percent / Decimal("100")
        client_surcharge_value_2 = split_amount_2 * client_surcharge_percent_2 / Decimal("100")
        client_surcharge_value   = client_surcharge_value_1 + client_surcharge_value_2

        installment_value_1 = (split_amount_1 + client_surcharge_value_1) / Decimal(sim_installments) if sim_installments > 1 else (split_amount_1 + client_surcharge_value_1)
        installment_value_2 = (split_amount_2 + client_surcharge_value_2) / Decimal(sim_installments_2) if sim_installments_2 > 1 else (split_amount_2 + client_surcharge_value_2)
    else:
        split_1 = total_after_disc
        split_2 = Decimal("0")
        split_amount_1 = total_after_disc
        split_amount_2 = Decimal("0")
        payment_fee_value_1 = Decimal("0")
        payment_fee_value_2 = Decimal("0")
        client_surcharge_value_1 = Decimal("0")
        client_surcharge_value_2 = Decimal("0")
        installment_value_2 = Decimal("0")

        # Store absorbs the card fee — it is a cost on the margin, not a surcharge to the client
        payment_fee_value      = financed_value * store_fee_percent / Decimal("100")
        client_surcharge_value = Decimal("0")
        # Client pays the base financed amount split into installments (no fee on top)
        installment_value_1    = financed_value / Decimal(sim_installments) if sim_installments > 1 else financed_value

    # Client total = listed price (fee is absorbed by store margin, not passed to client)
    if split_mode:
        final_total = split_amount_1 + split_amount_2 + client_surcharge_value
    else:
        final_total = total_after_disc
    # Legacy single-method installment (for non-split)
    installment_value = installment_value_1 if not split_mode else Decimal("0")

    # Net received by store (before commission — base product value minus fees)
    valor_avista = total_after_disc - payment_fee_value

    # ── Seller commission ────────────────────────────────────────────────
    # Base = raw subtotal (brute value, before price increase).
    # Price increase is a cost-recovery mechanism, not part of the commission base.
    seller_commission_base = subtotal
    seller_discount_value  = subtotal * sim_discount / Decimal("100")
    seller_commission_base = max(Decimal("0"), seller_commission_base - seller_discount_value)
    seller_commission_value = seller_commission_base * seller_commission_percent / Decimal("100")

    # ── Architect commission ─────────────────────────────────────────────
    # Base = subtotal (no freight)
    #      - store margin amount  (subtotal × MARGIN_BASE%)
    # Seller commission is informational only and does NOT affect this base.
    # Example: 15,000 − 1,500 (10%) = 13,500 → 5% = 675
    if sim_has_architect:
        _arch_store_deduction      = subtotal * MARGIN_BASE / Decimal("100")
        _arch_base                 = max(Decimal("0"), subtotal - _arch_store_deduction)
        architect_commission_value = _arch_base * architect_percent / Decimal("100")

    # Payment selects
    payment_type_choices = list(PaymentMethodType.choices)
    max_inst_map = {
        'CASH': 1, 'PIX': 1, 'DEBIT_CARD': 1, 'CREDIT_CARD': 18, 'CHEQUE': 1, 'BOLETO': 18,
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
            else:
                lbl = f"{i}x"
            options.append({'installments': i, 'fee': fee, 'label': lbl})
        tariffs_by_type[pt_val] = options

    # Payment description
    if sim_payment_type:
        pt_label = dict(PaymentMethodType.choices).get(sim_payment_type, sim_payment_type)
        desc1 = f"{pt_label} - À vista" if sim_installments == 1 else f"{pt_label} - {sim_installments}x"
        if split_mode and sim_payment_type_2:
            pt_label2 = dict(PaymentMethodType.choices).get(sim_payment_type_2, sim_payment_type_2)
            desc2 = f"{pt_label2} - À vista" if sim_installments_2 == 1 else f"{pt_label2} - {sim_installments_2}x"
            sim_payment_description = f"{desc1} + {desc2}"
        else:
            desc2 = ""
            sim_payment_description = desc1
    else:
        desc1 = ""
        desc2 = ""
        sim_payment_description = "Não definido"

    return {
        'subtotal':                 subtotal,
        'adj_subtotal':             adj_subtotal,
        'price_increase_value':     adj_subtotal - subtotal,
        'freight_value':            freight_value,
        'price_increase_pct':       price_increase_pct,
        'total_before_discount':    total_before_disc,
        'discount_percent':         sim_discount,
        'discount_value':           discount_value,
        'total_after_discount':     total_after_disc,
        'payment_fee_percent':      payment_fee_percent,
        'store_fee_percent':        store_fee_percent,
        'payment_fee_value':        payment_fee_value,
        'financed_ratio_pct':       (_dp_fin_ratio * Decimal("100")).quantize(Decimal("1")),
        'down_payment_value':       down_payment_used,
        'dp_min_value':             dp_min_value,
        'financed_value':           financed_value,
        'client_surcharge_percent': client_surcharge_percent,
        'client_surcharge_value':   client_surcharge_value,
        'final_total':              final_total,
        'installment_value':        installment_value,
        'seller_commission_percent': seller_commission_percent,
        'seller_commission_value':   seller_commission_value,
        'seller_commission_base':    seller_commission_base,
        'original_commission_percent': original_commission_percent,
        'commission_floor':            COMMISSION_FLOOR,
        'commission_max':              COMMISSION_MAX,
        'commission_reduced':          commission_reduced,
        'margin_base':               MARGIN_BASE,
        'effective_margin':          effective_margin,
        'total_cost_pct':            total_cost_pct,
        'margin_exceeded':           margin_exceeded,
        'margin_excess':             margin_excess,
        'controls_blocked':          controls_blocked,
        'margin_limit':              margin_limit,
        'margin_limit_exceeded':     margin_limit_exceeded,
        'min_increase_to_unblock':   min_increase_to_unblock,
        'max_discount_allowed':      max_discount_allowed,

        'suggested_increase':        suggested_increase,
        'suggestion_is_opportunity': suggestion_is_opportunity,
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
        'target_mode':               target_mode,
        'target_final_input':        target_final if target_final else Decimal("0"),
        'target_installment_mode':   target_installment_mode,
        'target_installment_input':  target_installment if target_installment else Decimal("0"),
        # split payment
        'split_mode':                split_mode,
        'sim_payment_type_2':        sim_payment_type_2,
        'sim_installments_2':        sim_installments_2,
        'payment_fee_percent_2':     payment_fee_percent_2,
        'store_fee_percent_2':       store_fee_percent_2,
        'payment_fee_value_2':       payment_fee_value_2,
        'client_surcharge_percent_2': client_surcharge_percent_2,
        'client_surcharge_value_1':  client_surcharge_value_1,
        'client_surcharge_value_2':  client_surcharge_value_2,
        'split_amount_1':            split_amount_1,
        'split_amount_2':            split_amount_2,
        'installment_value_1':       installment_value_1,
        'installment_value_2':       installment_value_2,
        'sim_split_amount':          sim_split_amount,
        'sim_payment_desc_1':        desc1,
        'sim_payment_desc_2':        desc2,
        # per-method price increase (split mode)
        'price_increase_pct_2':      price_increase_pct_2,
        'split_m1_avista':           _split_m1_avista,
        'split_both_cards':          split_both_cards,
        'margin_exceeded_1':         margin_exceeded_1,
        'margin_exceeded_2':         margin_exceeded_2,
        'any_method_over_margin':    any_method_over_margin,
        'blended_fee_pct':           _blended_fee,
        'suggested_increase_1':      suggested_increase_1,
        'suggested_increase_2':      suggested_increase_2,
    }


@login_required
@require_http_methods(["GET", "POST"])
def quote_simulate_commission(request: HttpRequest, quote_id: int) -> HttpResponse:
    quote = get_object_or_404(
        Quote.objects.select_related('customer', 'seller'), id=quote_id
    )
    if not _is_staff_or_admin(request.user) and quote.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:quote_list")

    subtotal = quote.calculate_subtotal()
    freight_value = quote.freight_value or Decimal("0.00")

    if request.method == "POST":
        sim_payment_type    = request.POST.get('sim_payment_type', '') or ''
        sim_has_architect   = request.POST.get('sim_has_architect') == '1'
        sim_architect_id    = request.POST.get('sim_architect_id', '') or ''
        try:
            sim_discount = Decimal(request.POST.get('discount_percent', '0') or '0')
        except Exception:
            sim_discount = Decimal('0')
        try:
            price_increase_pct = Decimal(request.POST.get('price_increase_percent', '0') or '0')
        except Exception:
            price_increase_pct = Decimal('0')
        sim_installments    = max(1, int(request.POST.get('sim_installments', '1') or 1))
        # Target final: reverse calculation mode
        try:
            target_final = Decimal(request.POST.get('target_final', '') or '0')
            if target_final <= 0:
                target_final = None
        except Exception:
            target_final = None
        # Target installment: parcela desejada
        try:
            target_installment = Decimal(request.POST.get('target_installment', '') or '0')
            if target_installment <= 0:
                target_installment = None
        except Exception:
            target_installment = None
        # Split payment
        sim_payment_type_2 = request.POST.get('sim_payment_type_2', '') or ''
        sim_installments_2 = max(1, int(request.POST.get('sim_installments_2', '1') or 1))
        try:
            price_increase_pct_2 = Decimal(request.POST.get('price_increase_percent_2', '0') or '0')
        except Exception:
            price_increase_pct_2 = Decimal('0')
        try:
            _sa = request.POST.get('sim_split_amount', '') or ''
            sim_split_amount = Decimal(_sa) if _sa else None
            if sim_split_amount is not None and sim_split_amount <= 0:
                sim_split_amount = None
        except Exception:
            sim_split_amount = None
        try:
            _dp = request.POST.get('down_payment_value', '') or ''
            down_payment_value = Decimal(_dp) if _dp else None
            if down_payment_value is not None and down_payment_value <= 0:
                down_payment_value = None
        except Exception:
            down_payment_value = None
    else:
        sim_payment_type    = quote.payment_type or ''
        sim_has_architect   = quote.has_architect
        sim_architect_id    = str(quote.architect_id or '')
        sim_discount        = quote.discount_percent or Decimal("0")
        price_increase_pct  = Decimal('0')
        sim_installments    = quote.payment_installments or 1
        target_final        = None
        target_installment  = None
        sim_payment_type_2  = quote.payment_type_2 or ''
        sim_installments_2  = quote.payment_installments_2 or 1
        sim_split_amount    = quote.payment_split_amount
        price_increase_pct_2 = Decimal('0')
        down_payment_value  = None

    # Resolve architect
    from core.models import Architect
    selected_architect = None
    if sim_architect_id:
        try:
            selected_architect = Architect.objects.get(pk=int(sim_architect_id))
        except (Architect.DoesNotExist, ValueError, TypeError):
            pass

    ctx = _build_simulation_context(
        subtotal=subtotal,
        freight_value=freight_value,
        sim_payment_type=sim_payment_type,
        sim_has_architect=sim_has_architect,
        sim_discount=sim_discount,
        price_increase_pct=price_increase_pct,
        sim_installments=sim_installments,
        target_final=target_final,
        target_installment=target_installment,
        sim_payment_type_2=sim_payment_type_2,
        sim_installments_2=sim_installments_2,
        sim_split_amount=sim_split_amount,
        price_increase_pct_2=price_increase_pct_2,
        down_payment_value=down_payment_value,
    )

    save_session_key = f"quote_sim_saved_{quote.id}"
    quote_actions_unlocked = bool(request.session.get(save_session_key, False))

    if request.method == "POST" and request.POST.get('action') == 'save_conditions':
        if ctx['margin_limit_exceeded']:
            messages.error(request, "Condições bloqueadas. Ajuste o preço antes de salvar.")
            ctx['quote'] = quote
            ctx['quote_actions_unlocked'] = quote_actions_unlocked
            return render(request, 'sales/quote_simulation.html', ctx)
        with transaction.atomic():
            quote.discount_percent       = ctx['discount_percent']
            quote.payment_type           = ctx['sim_payment_type']
            quote.payment_installments   = ctx['sim_installments']
            quote.payment_fee_percent    = ctx['payment_fee_percent']
            # split payment
            if ctx['split_mode']:
                quote.payment_type_2         = ctx['sim_payment_type_2']
                quote.payment_installments_2 = ctx['sim_installments_2']
                quote.payment_fee_percent_2  = ctx['payment_fee_percent_2']
                quote.payment_split_amount   = ctx['split_amount_1']
            else:
                quote.payment_type_2         = ''
                quote.payment_installments_2 = 1
                quote.payment_fee_percent_2  = Decimal("0.00")
                quote.payment_split_amount   = None
            quote.has_architect          = ctx['sim_has_architect']
            if not ctx['sim_has_architect']:
                quote.architect = None  # clear if toggled off; keep existing when toggled on
            quote.save()
        request.session[save_session_key] = True
        messages.success(request, f"Condições do orçamento {quote.number} salvas com sucesso.")
        return redirect("sales:quote_detail", quote_id=quote.id)

    if request.method == "POST" and not request.POST.get('_ajax'):
        return redirect(request.path)

    ctx['quote'] = quote
    ctx['selected_architect'] = selected_architect
    ctx['sim_architect_id']   = sim_architect_id
    ctx['quote_actions_unlocked'] = quote_actions_unlocked
    ctx['can_view_commission'] = _can_view_commission(request.user)
    ctx['is_admin'] = _is_admin(request.user)
    return render(request, 'sales/quote_simulation.html', ctx)


# ──────────────────────────────────────────────────────────────────────
# Standalone Simulation (sem orçamento)
# ──────────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["GET", "POST"])
def standalone_simulation(request: HttpRequest) -> HttpResponse:
    """Simulação rápida de valores — sem precisar criar orçamento."""
    from core.models import Customer, Architect

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
        sim_architect_id = request.POST.get('sim_architect_id', '') or ''
        # Target final: reverse calculation mode
        try:
            target_final = Decimal(request.POST.get('target_final', '') or '0')
            if target_final <= 0:
                target_final = None
        except Exception:
            target_final = None
        # Target installment: parcela desejada
        try:
            target_installment = Decimal(request.POST.get('target_installment', '') or '0')
            if target_installment <= 0:
                target_installment = None
        except Exception:
            target_installment = None
        # Split payment
        sim_payment_type_2 = request.POST.get('sim_payment_type_2', '') or ''
        sim_installments_2 = max(1, int(request.POST.get('sim_installments_2', '1') or 1))
        try:
            price_increase_pct_2 = Decimal(request.POST.get('price_increase_percent_2', '0') or '0')
        except Exception:
            price_increase_pct_2 = Decimal('0')
        try:
            _sa = request.POST.get('sim_split_amount', '') or ''
            sim_split_amount = Decimal(_sa) if _sa else None
            if sim_split_amount is not None and sim_split_amount <= 0:
                sim_split_amount = None
        except Exception:
            sim_split_amount = None
        try:
            _dp = request.POST.get('down_payment_value', '') or ''
            down_payment_value = Decimal(_dp) if _dp else None
            if down_payment_value is not None and down_payment_value <= 0:
                down_payment_value = None
        except Exception:
            down_payment_value = None
    else:
        subtotal           = Decimal('0')
        freight_value      = Decimal('0')
        sim_payment_type   = ''
        sim_has_architect  = False
        sim_discount       = Decimal('0')
        price_increase_pct = Decimal('0')
        sim_installments   = 1
        customer_id        = ''
        sim_architect_id   = ''
        target_final       = None
        target_installment = None
        sim_payment_type_2   = ''
        sim_installments_2   = 1
        sim_split_amount     = None
        price_increase_pct_2 = Decimal('0')
        down_payment_value   = None

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

    # Resolve architect
    selected_architect = None
    if sim_architect_id:
        try:
            selected_architect = Architect.objects.get(pk=int(sim_architect_id))
        except (Architect.DoesNotExist, ValueError, TypeError):
            pass

    ctx = _build_simulation_context(
        subtotal=subtotal,
        freight_value=freight_value,
        sim_payment_type=sim_payment_type,
        sim_has_architect=sim_has_architect,
        sim_discount=sim_discount,
        price_increase_pct=price_increase_pct,
        sim_installments=sim_installments,
        target_final=target_final,
        target_installment=target_installment,
        sim_payment_type_2=sim_payment_type_2,
        sim_installments_2=sim_installments_2,
        sim_split_amount=sim_split_amount,
        price_increase_pct_2=price_increase_pct_2,
        down_payment_value=down_payment_value,
    )
    ctx['standalone']         = True
    ctx['sim_subtotal']       = subtotal
    ctx['sim_freight']        = freight_value
    ctx['selected_customer']  = selected_customer
    ctx['sim_customer_id']    = customer_id
    ctx['selected_architect'] = selected_architect
    ctx['sim_architect_id']   = sim_architect_id
    ctx['can_view_commission'] = _can_view_commission(request.user)

    return render(request, 'sales/standalone_simulation.html', ctx)


# ──────────────────────────────────────────────────────────────────────
# Duplicate Quote
# ──────────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["POST"])
def quote_duplicate(request, quote_id):
    """Duplicar um orçamento existente."""
    original = get_object_or_404(Quote, id=quote_id)
    if not _is_staff_or_admin(request.user) and original.seller_id != request.user.id:
        messages.error(request, "Acesso negado.")
        return redirect("sales:quote_list")
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


@login_required
@require_http_methods(["POST"])
def quote_delete(request, quote_id):
    """Excluir um orçamento. Apenas admins/donos podem excluir."""
    from django.db.models.deletion import ProtectedError

    if not _is_admin(request.user):
        messages.error(request, "Apenas administradores podem excluir orçamentos.")
        return redirect("sales:quote_detail", quote_id=quote_id)

    quote = get_object_or_404(Quote, id=quote_id)
    number = quote.number
    try:
        with transaction.atomic():
            # Order.quote is PROTECT; remove dependent orders first.
            deleted_orders = quote.orders.count()
            quote.orders.all().delete()
            quote.delete()
    except ProtectedError:
        messages.error(
            request,
            "Não foi possível excluir o orçamento por dependências vinculadas.",
        )
        return redirect("sales:quote_detail", quote_id=quote_id)

    from core.models import AuditLog, AuditAction
    AuditLog.log(request.user, AuditAction.DELETE_QUOTE,
                 f"Orçamento excluído: {number} (pedidos removidos: {deleted_orders})", obj=None)
    if deleted_orders:
        messages.success(request, f"Orçamento {number} excluído com sucesso (incluindo {deleted_orders} pedido(s) vinculado(s)).")
    else:
        messages.success(request, f"Orçamento {number} excluído com sucesso.")
    return redirect("sales:quote_list")
