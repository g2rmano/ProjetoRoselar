from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import QuoteForm, QuoteItemFormSet
from .models import (
    Quote,
    QuoteStatus,
    QuoteItemImage,
    Order,
    OrderItem,
)


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
                quote.save()

                formset.instance = quote
                formset.save()

            messages.success(request, f"Orçamento {quote.number} criado.")
            return redirect("sales:quote_detail", quote_id=quote.id)
        else:
            messages.error(request, "Corrija os campos inválidos.")
    else:
        form = QuoteForm()
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
                form.save()
                formset.save()

            messages.success(request, f"Orçamento {quote.number} atualizado.")
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

        messages.success(request, f"Orçamento {quote.number} convertido em pedidos.")
        return redirect("sales:quote_detail", quote_id=quote.id)

    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("sales:quote_detail", quote_id=quote.id)
