from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, F, Avg


def health_check(request):
    return HttpResponse("ok", content_type="text/plain")
from django.db.models.functions import TruncMonth
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from .models import (
    Customer, ShippingCompany,
    Notification, NotificationType,
    AuditLog, AuditAction,
    SalesGoal, GoalType,
    Lead, LeadStage, LeadSource, LeadInteraction,
    CommunicationHistory,
    QuoteTemplate, QuoteTemplateItem,
)
from sales.models import Quote, QuoteStatus, Order, OrderStatus, QuoteItem
from accounts.models import User, Role
from calendar_app.models import CalendarEvent, EventStatus


# ──────────────────────────────────────────────────────────────────────
# Home
# ──────────────────────────────────────────────────────────────────────
def home(request):
    context = {}
    if request.user.is_authenticated:
        user = request.user
        today = timezone.localdate()
        month_start = today.replace(day=1)
        is_admin = user.role in (Role.ADMIN, Role.OWNER) or user.is_superuser

        my_quotes = Quote.objects.filter(seller=user)
        my_quotes_month = my_quotes.filter(quote_date__gte=month_start)
        my_converted = my_quotes.filter(status=QuoteStatus.CONVERTED)
        my_converted_month = my_converted.filter(quote_date__gte=month_start)

        my_total_sold_month = my_converted_month.aggregate(
            total=Sum("total_value_snapshot")
        )["total"] or Decimal("0")
        my_quotes_count_month = my_quotes_month.count()
        my_converted_count_month = my_converted_month.count()
        my_conversion_rate = (
            round(my_converted_count_month / my_quotes_count_month * 100, 1)
            if my_quotes_count_month > 0 else 0
        )
        my_avg_ticket = (
            round(my_total_sold_month / my_converted_count_month, 2)
            if my_converted_count_month > 0 else Decimal("0")
        )

        # Previous month
        prev_month_end = month_start - timedelta(days=1)
        prev_month_start = prev_month_end.replace(day=1)
        prev_total = my_quotes.filter(
            status=QuoteStatus.CONVERTED,
            quote_date__gte=prev_month_start,
            quote_date__lte=prev_month_end,
        ).aggregate(total=Sum("total_value_snapshot"))["total"] or Decimal("0")

        # Goal
        my_goal = SalesGoal.objects.filter(
            seller=user, period_start__lte=today, period_end__gte=today
        ).first()
        goal_target = my_goal.target_value if my_goal else user.individual_target_value or Decimal("0")
        goal_pct = round(float(my_total_sold_month) / float(goal_target) * 100, 1) if goal_target > 0 else 0

        # Avg discount
        avg_discount = (
            my_converted_month.aggregate(avg=Avg("discount_percent"))["avg"] or 0
        )

        # Pending quotes
        pending_quotes = (
            Quote.objects.filter(status=QuoteStatus.DRAFT)
            .select_related("customer", "seller")
            .order_by("-quote_date")[:5]
        )

        # Upcoming deliveries
        upcoming_deliveries = (
            CalendarEvent.objects.filter(
                event_type="DELIVERY",
                status=EventStatus.PENDING,
                event_date__gte=today,
                event_date__lte=today + timedelta(days=7),
            )
            .order_by("event_date")[:5]
        )

        # Overdue
        overdue_events = (
            CalendarEvent.objects.filter(
                status=EventStatus.PENDING,
                event_date__lt=today,
            )
            .order_by("event_date")[:5]
        )

        # Notifications
        unread_count = Notification.objects.filter(recipient=user, read=False).count()

        # Monthly chart (6 months)
        six_months_ago = (today - timedelta(days=180)).replace(day=1)
        monthly_data = (
            Quote.objects.filter(
                status=QuoteStatus.CONVERTED,
                seller=user,
                quote_date__gte=six_months_ago,
            )
            .annotate(month=TruncMonth("quote_date"))
            .values("month")
            .annotate(total=Sum("total_value_snapshot"), count=Count("id"))
            .order_by("month")
        )
        chart_labels = [d["month"].strftime("%b/%y") for d in monthly_data]
        chart_values = [float(d["total"] or 0) for d in monthly_data]

        # Personal status breakdown (for hero pie)
        my_status_data = (
            my_quotes_month.values("status").annotate(count=Count("id"))
        )
        my_status_labels = [QuoteStatus(d["status"]).label for d in my_status_data]
        my_status_values = [d["count"] for d in my_status_data]

        # Team stats (admin)
        team_total_sold_month = Decimal("0")
        team_quotes_month = 0
        team_conversion_rate = 0
        seller_ranking = []
        collective_goal = None
        collective_goal_pct = 0
        bi_team_chart_labels = bi_team_chart_values = bi_team_chart_counts = []
        bi_status_labels = bi_status_values = []
        bi_prod_labels = bi_prod_values = []
        bi_funnel_labels = bi_funnel_values = []
        bi_seller_labels = bi_seller_values = bi_seller_counts = []
        bi_disc_labels = bi_disc_values = []
        bi_top_products = []
        if is_admin:
            all_conv = Quote.objects.filter(status=QuoteStatus.CONVERTED, quote_date__gte=month_start)
            team_total_sold_month = all_conv.aggregate(total=Sum("total_value_snapshot"))["total"] or Decimal("0")
            team_quotes_month = Quote.objects.filter(quote_date__gte=month_start).count()
            team_converted_month = all_conv.count()
            team_conversion_rate = (
                round(team_converted_month / team_quotes_month * 100, 1)
                if team_quotes_month > 0 else 0
            )
            seller_ranking = (
                Quote.objects.filter(status=QuoteStatus.CONVERTED, quote_date__gte=month_start)
                .values("seller__username")
                .annotate(total=Sum("total_value_snapshot"), count=Count("id"))
                .order_by("-total")[:10]
            )
            collective_goal = SalesGoal.objects.filter(
                goal_type=GoalType.COLLECTIVE, period_start__lte=today, period_end__gte=today,
            ).first()
            if collective_goal and collective_goal.target_value:
                collective_goal_pct = round(
                    float(team_total_sold_month) / float(collective_goal.target_value) * 100, 1
                )

            # ── BI: Team monthly evolution (last 6 months) ──
            bi_team_monthly = (
                Quote.objects.filter(
                    status=QuoteStatus.CONVERTED,
                    quote_date__gte=six_months_ago,
                )
                .annotate(month=TruncMonth("quote_date"))
                .values("month")
                .annotate(total=Sum("total_value_snapshot"), count=Count("id"))
                .order_by("month")
            )
            bi_team_chart_labels = [d["month"].strftime("%b/%y") for d in bi_team_monthly]
            bi_team_chart_values = [float(d["total"] or 0) for d in bi_team_monthly]
            bi_team_chart_counts = [d["count"] for d in bi_team_monthly]

            # ── BI: Quote status breakdown ──
            bi_status_data = (
                Quote.objects.filter(quote_date__gte=month_start)
                .values("status")
                .annotate(count=Count("id"))
            )
            bi_status_labels = [QuoteStatus(d["status"]).label for d in bi_status_data]
            bi_status_values = [d["count"] for d in bi_status_data]

            # ── BI: Top 10 products by revenue ──
            bi_top_products = list(
                QuoteItem.objects.filter(
                    quote__status=QuoteStatus.CONVERTED,
                    quote__quote_date__gte=month_start,
                )
                .values("product_name")
                .annotate(
                    total_revenue=Sum(F("quantity") * F("unit_value")),
                    total_qty=Sum("quantity"),
                )
                .order_by("-total_revenue")[:10]
            )
            bi_prod_labels = [p["product_name"][:25] for p in bi_top_products]
            bi_prod_values = [float(p["total_revenue"] or 0) for p in bi_top_products]

            # ── BI: Leads funnel ──
            from .models import LeadStage as LS
            bi_leads_funnel = dict(
                Lead.objects.values_list("stage").annotate(c=Count("id")).values_list("stage", "c")
            )
            bi_funnel_labels = [LS(s).label for s in [LS.NEW, LS.CONTACTED, LS.QUOTE_SENT, LS.NEGOTIATION, LS.WON, LS.LOST]]
            bi_funnel_values = [bi_leads_funnel.get(s, 0) for s in [LS.NEW, LS.CONTACTED, LS.QUOTE_SENT, LS.NEGOTIATION, LS.WON, LS.LOST]]

            # ── BI: Seller comparison (bar chart) ──
            bi_seller_labels = [s["seller__username"] for s in seller_ranking]
            bi_seller_values = [float(s["total"] or 0) for s in seller_ranking]
            bi_seller_counts = [s["count"] for s in seller_ranking]

            # ── BI: Avg discount per seller ──
            bi_discount_data = list(
                Quote.objects.filter(
                    status=QuoteStatus.CONVERTED,
                    quote_date__gte=month_start,
                    discount_percent__gt=0,
                )
                .values("seller__username")
                .annotate(avg_disc=Avg("discount_percent"))
                .order_by("-avg_disc")[:10]
            )
            bi_disc_labels = [d["seller__username"] for d in bi_discount_data]
            bi_disc_values = [round(float(d["avg_disc"]), 1) for d in bi_discount_data]

        context = {
            "today": today,
            "is_admin": is_admin,
            "my_total_sold_month": my_total_sold_month,
            "my_quotes_count_month": my_quotes_count_month,
            "my_converted_count_month": my_converted_count_month,
            "my_conversion_rate": my_conversion_rate,
            "my_avg_ticket": my_avg_ticket,
            "prev_total": prev_total,
            "avg_discount": round(avg_discount, 1),
            "goal_target": goal_target,
            "goal_pct": min(goal_pct, 100),
            "goal_pct_raw": goal_pct,
            "pending_quotes": pending_quotes,
            "upcoming_deliveries": upcoming_deliveries,
            "overdue_events": overdue_events,
            "unread_count": unread_count,
            "chart_labels_json": json.dumps(chart_labels),
            "chart_values_json": json.dumps(chart_values),
            "my_status_labels_json": json.dumps(my_status_labels),
            "my_status_values_json": json.dumps(my_status_values),
            "team_total_sold_month": team_total_sold_month,
            "team_quotes_month": team_quotes_month,
            "team_conversion_rate": team_conversion_rate,
            "seller_ranking": seller_ranking,
            "collective_goal": collective_goal,
            "collective_goal_pct": min(collective_goal_pct, 100),
            # BI Charts (admin only)
            "bi_team_chart_labels_json": json.dumps(bi_team_chart_labels),
            "bi_team_chart_values_json": json.dumps(bi_team_chart_values),
            "bi_team_chart_counts_json": json.dumps(bi_team_chart_counts),
            "bi_status_labels_json": json.dumps(bi_status_labels),
            "bi_status_values_json": json.dumps(bi_status_values),
            "bi_prod_labels_json": json.dumps(bi_prod_labels),
            "bi_prod_values_json": json.dumps(bi_prod_values),
            "bi_funnel_labels_json": json.dumps(bi_funnel_labels),
            "bi_funnel_values_json": json.dumps(bi_funnel_values),
            "bi_seller_labels_json": json.dumps(bi_seller_labels),
            "bi_seller_values_json": json.dumps(bi_seller_values),
            "bi_seller_counts_json": json.dumps(bi_seller_counts),
            "bi_disc_labels_json": json.dumps(bi_disc_labels),
            "bi_disc_values_json": json.dumps(bi_disc_values),
            "bi_top_products": bi_top_products,
        }
    return render(request, "core/index.html", context)


# ──────────────────────────────────────────────────────────────────────
# Dashboard with Metrics
# ──────────────────────────────────────────────────────────────────────
@login_required
def dashboard(request):
    user = request.user
    today = timezone.localdate()
    month_start = today.replace(day=1)
    is_admin = user.role in (Role.ADMIN, Role.OWNER) or user.is_superuser

    # ── Personal Stats ──
    my_quotes = Quote.objects.filter(seller=user)
    my_quotes_month = my_quotes.filter(quote_date__gte=month_start)
    my_converted = my_quotes.filter(status=QuoteStatus.CONVERTED)
    my_converted_month = my_converted.filter(quote_date__gte=month_start)

    my_total_sold_month = my_converted_month.aggregate(
        total=Sum("total_value_snapshot")
    )["total"] or Decimal("0")
    my_quotes_count_month = my_quotes_month.count()
    my_converted_count_month = my_converted_month.count()
    my_conversion_rate = (
        round(my_converted_count_month / my_quotes_count_month * 100, 1)
        if my_quotes_count_month > 0 else 0
    )
    my_avg_ticket = (
        round(my_total_sold_month / my_converted_count_month, 2)
        if my_converted_count_month > 0 else Decimal("0")
    )

    # ── Previous month comparison ──
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    prev_converted = my_quotes.filter(
        status=QuoteStatus.CONVERTED,
        quote_date__gte=prev_month_start,
        quote_date__lte=prev_month_end,
    )
    prev_total = prev_converted.aggregate(total=Sum("total_value_snapshot"))["total"] or Decimal("0")

    # ── My Goal ──
    my_goal = SalesGoal.objects.filter(
        seller=user, period_start__lte=today, period_end__gte=today
    ).first()
    goal_target = my_goal.target_value if my_goal else user.individual_target_value or Decimal("0")
    goal_pct = round(float(my_total_sold_month) / float(goal_target) * 100, 1) if goal_target > 0 else 0

    # ── Team Stats (admin) ──
    team_total_sold_month = Decimal("0")
    team_quotes_month = 0
    team_converted_month = 0
    team_conversion_rate = 0
    seller_ranking = []
    collective_goal = None
    collective_goal_pct = 0

    if is_admin:
        all_converted_month = Quote.objects.filter(
            status=QuoteStatus.CONVERTED, quote_date__gte=month_start
        )
        team_total_sold_month = all_converted_month.aggregate(
            total=Sum("total_value_snapshot")
        )["total"] or Decimal("0")
        team_quotes_month = Quote.objects.filter(quote_date__gte=month_start).count()
        team_converted_month = all_converted_month.count()
        team_conversion_rate = (
            round(team_converted_month / team_quotes_month * 100, 1)
            if team_quotes_month > 0 else 0
        )

        # Ranking
        seller_ranking = (
            Quote.objects.filter(status=QuoteStatus.CONVERTED, quote_date__gte=month_start)
            .values("seller__username")
            .annotate(total=Sum("total_value_snapshot"), count=Count("id"))
            .order_by("-total")[:10]
        )

        # Collective goal
        collective_goal = SalesGoal.objects.filter(
            goal_type=GoalType.COLLECTIVE,
            period_start__lte=today,
            period_end__gte=today,
        ).first()
        if collective_goal and collective_goal.target_value:
            collective_goal_pct = round(
                float(team_total_sold_month) / float(collective_goal.target_value) * 100, 1
            )

    # ── Monthly evolution (last 6 months) ──
    six_months_ago = (today - timedelta(days=180)).replace(day=1)
    monthly_data = (
        Quote.objects.filter(
            status=QuoteStatus.CONVERTED,
            seller=user,
            quote_date__gte=six_months_ago,
        )
        .annotate(month=TruncMonth("quote_date"))
        .values("month")
        .annotate(total=Sum("total_value_snapshot"), count=Count("id"))
        .order_by("month")
    )
    chart_labels = [d["month"].strftime("%b/%y") for d in monthly_data]
    chart_values = [float(d["total"] or 0) for d in monthly_data]

    # ── Pending quotes ──
    pending_quotes = (
        Quote.objects.filter(status=QuoteStatus.DRAFT)
        .select_related("customer", "seller")
        .order_by("-quote_date")[:5]
    )

    # ── Upcoming deliveries ──
    upcoming_deliveries = (
        CalendarEvent.objects.filter(
            event_type="DELIVERY",
            status=EventStatus.PENDING,
            event_date__gte=today,
            event_date__lte=today + timedelta(days=7),
        )
        .select_related("customer", "assigned_to")
        .order_by("event_date")[:5]
    )

    # ── Overdue events ──
    overdue_events = (
        CalendarEvent.objects.filter(
            status=EventStatus.PENDING,
            event_date__lt=today,
        )
        .select_related("customer", "assigned_to")
        .order_by("event_date")[:5]
    )

    # ── Notifications count ──
    unread_count = Notification.objects.filter(recipient=user, read=False).count()

    # ── Leads summary ──
    leads_by_stage = dict(
        Lead.objects.filter(seller=user)
        .values_list("stage")
        .annotate(c=Count("id"))
        .values_list("stage", "c")
    )

    # ── Avg discount ──
    avg_discount = (
        my_converted_month.aggregate(avg=Avg("discount_percent"))["avg"] or 0
    )

    context = {
        "today": today,
        "is_admin": is_admin,
        # Personal
        "my_total_sold_month": my_total_sold_month,
        "my_quotes_count_month": my_quotes_count_month,
        "my_converted_count_month": my_converted_count_month,
        "my_conversion_rate": my_conversion_rate,
        "my_avg_ticket": my_avg_ticket,
        "prev_total": prev_total,
        "avg_discount": round(avg_discount, 1),
        # Goal
        "my_goal": my_goal,
        "goal_target": goal_target,
        "goal_pct": min(goal_pct, 100),
        "goal_pct_raw": goal_pct,
        # Team
        "team_total_sold_month": team_total_sold_month,
        "team_quotes_month": team_quotes_month,
        "team_converted_month": team_converted_month,
        "team_conversion_rate": team_conversion_rate,
        "seller_ranking": seller_ranking,
        "collective_goal": collective_goal,
        "collective_goal_pct": min(collective_goal_pct, 100),
        # Chart
        "chart_labels_json": json.dumps(chart_labels),
        "chart_values_json": json.dumps(chart_values),
        # Lists
        "pending_quotes": pending_quotes,
        "upcoming_deliveries": upcoming_deliveries,
        "overdue_events": overdue_events,
        "unread_count": unread_count,
        "leads_by_stage": leads_by_stage,
    }
    return render(request, "core/dashboard.html", context)


# ──────────────────────────────────────────────────────────────────────
# Customer APIs (existing)
# ──────────────────────────────────────────────────────────────────────
@login_required
def search_customer(request):
    document = request.GET.get("document", "").strip()
    if not document:
        return JsonResponse({"found": False})
    customer = (
        Customer.objects.filter(cpf=document).first()
        or Customer.objects.filter(cnpj=document).first()
    )
    if customer:
        return JsonResponse({
            "found": True, "id": customer.id, "name": customer.name,
            "cpf": customer.cpf, "cnpj": customer.cnpj,
            "phone": customer.phone, "email": customer.email,
        })
    return JsonResponse({"found": False})


@login_required
@require_http_methods(["POST"])
def create_customer(request):
    try:
        data = json.loads(request.body)
        customer = Customer.objects.create(
            name=data.get("name"), cpf=data.get("cpf", ""),
            cnpj=data.get("cnpj", ""), phone=data.get("phone", ""),
            email=data.get("email", ""),
        )
        return JsonResponse({"success": True, "customer": {"id": customer.id, "name": str(customer)}})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=400)


@login_required
def search_customer_by_name(request):
    query = request.GET.get("query", "").strip()
    if not query or len(query) < 2:
        return JsonResponse({"results": []})
    customers = Customer.objects.filter(name__icontains=query)[:3]
    results = [
        {"id": c.id, "name": c.name, "display": str(c), "cpf": c.cpf or "", "cnpj": c.cnpj or ""}
        for c in customers
    ]
    return JsonResponse({"results": results})


@login_required
def get_shipping_company_payment_methods(request, company_id):
    try:
        company = ShippingCompany.objects.get(id=company_id, is_active=True)
        return JsonResponse({"success": True, "payment_methods": company.payment_methods or ""})
    except ShippingCompany.DoesNotExist:
        return JsonResponse({"success": False, "payment_methods": ""}, status=404)


# ──────────────────────────────────────────────────────────────────────
# Global Search
# ──────────────────────────────────────────────────────────────────────
@login_required
def global_search(request):
    q = request.GET.get("q", "").strip()
    if not q or len(q) < 2:
        return JsonResponse({"results": []})

    results = []

    # Customers
    for c in Customer.objects.filter(Q(name__icontains=q) | Q(cpf__icontains=q) | Q(cnpj__icontains=q))[:3]:
        results.append({"type": "Cliente", "title": str(c), "url": ""})

    # Quotes
    for qq in Quote.objects.filter(Q(number__icontains=q) | Q(customer__name__icontains=q)).select_related("customer")[:3]:
        results.append({
            "type": "Orçamento",
            "title": f"{qq.number} – {qq.customer.name}",
            "url": f"/sales/quotes/{qq.id}/",
        })

    # Orders
    for o in Order.objects.filter(Q(number__icontains=q)).select_related("quote")[:3]:
        results.append({
            "type": "Pedido",
            "title": f"OC {o.number}",
            "url": f"/sales/orders/{o.id}/",
        })

    # Leads
    for lead in Lead.objects.filter(Q(name__icontains=q) | Q(phone__icontains=q))[:3]:
        results.append({
            "type": "Lead",
            "title": str(lead),
            "url": f"/leads/{lead.id}/",
        })

    return JsonResponse({"results": results})


# ──────────────────────────────────────────────────────────────────────
# Notifications
# ──────────────────────────────────────────────────────────────────────
@login_required
def notifications_list(request):
    notifications = Notification.objects.filter(recipient=request.user).order_by("-created_at")[:50]
    return render(request, "core/notifications.html", {"notifications": notifications})


@login_required
def notifications_api(request):
    """Badge count + unread list for navbar dropdown."""
    qs = Notification.objects.filter(recipient=request.user, read=False).order_by("-created_at")[:10]
    data = {
        "unread_count": Notification.objects.filter(recipient=request.user, read=False).count(),
        "items": [
            {
                "id": n.id,
                "type": n.notification_type,
                "title": n.title,
                "message": n.message[:100],
                "url": n.url,
                "created_at": n.created_at.strftime("%d/%m %H:%M"),
            }
            for n in qs
        ],
    }
    return JsonResponse(data)


@login_required
@require_http_methods(["POST"])
def notification_mark_read(request, pk):
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    n.mark_as_read()
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def notification_mark_all_read(request):
    Notification.objects.filter(recipient=request.user, read=False).update(
        read=True, read_at=timezone.now()
    )
    return JsonResponse({"ok": True})


# ──────────────────────────────────────────────────────────────────────
# Leads & Pipeline
# ──────────────────────────────────────────────────────────────────────
@login_required
def leads_pipeline(request):
    user = request.user
    is_admin = user.role in (Role.ADMIN, Role.OWNER) or user.is_superuser

    qs = Lead.objects.select_related("seller", "customer").all()
    if not is_admin:
        qs = qs.filter(seller=user)

    stages = LeadStage.choices
    pipeline = {value: [] for value, label in stages}
    for lead in qs:
        pipeline.setdefault(lead.stage, []).append(lead)

    stage_counts = {value: len(pipeline.get(value, [])) for value, label in stages}

    return render(request, "core/leads_pipeline.html", {
        "pipeline": pipeline,
        "stages": stages,
        "stage_counts": stage_counts,
        "is_admin": is_admin,
        "sources": LeadSource.choices,
    })


@login_required
@require_http_methods(["POST"])
def lead_create(request):
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "Nome é obrigatório.")
        return redirect("core:leads_pipeline")

    lead = Lead.objects.create(
        name=name,
        phone=request.POST.get("phone", ""),
        email=request.POST.get("email", ""),
        source=request.POST.get("source", "OTHER"),
        seller=request.user,
        products_of_interest=request.POST.get("products_of_interest", ""),
        estimated_budget=request.POST.get("estimated_budget") or None,
        notes=request.POST.get("notes", ""),
    )
    AuditLog.log(request.user, AuditAction.CREATE_LEAD, f"Lead criado: {lead.name}", obj=lead,
                 ip_address=request.META.get("REMOTE_ADDR"))

    Notification.send(
        request.user, f"Novo lead: {lead.name}",
        NotificationType.NEW_LEAD, url=f"/leads/{lead.id}/",
    )

    messages.success(request, f"Lead '{lead.name}' criado.")
    return redirect("core:leads_pipeline")


@login_required
def lead_detail(request, pk):
    lead = get_object_or_404(Lead.objects.select_related("seller", "customer", "quote"), pk=pk)
    interactions = lead.interactions.select_related("created_by").all()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_interaction":
            LeadInteraction.objects.create(
                lead=lead,
                channel=request.POST.get("channel", "OTHER"),
                summary=request.POST.get("summary", ""),
                created_by=request.user,
            )
            lead.last_interaction = timezone.now()
            lead.save(update_fields=["last_interaction"])
            messages.success(request, "Interação registrada.")

        elif action == "change_stage":
            new_stage = request.POST.get("stage")
            if new_stage in dict(LeadStage.choices):
                lead.stage = new_stage
                lead.save(update_fields=["stage", "updated_at"])
                messages.success(request, f"Estágio alterado para {lead.get_stage_display()}.")

        elif action == "convert_to_customer":
            if not lead.customer:
                customer = Customer.objects.create(
                    name=lead.name,
                    phone=lead.phone,
                    email=lead.email,
                    cpf="",
                    cnpj="",
                )
                lead.customer = customer
                lead.stage = LeadStage.WON
                lead.save(update_fields=["customer", "stage", "updated_at"])
                AuditLog.log(request.user, AuditAction.CONVERT_LEAD,
                             f"Lead convertido em cliente: {lead.name}", obj=lead)
                messages.success(request, f"Lead convertido em cliente '{customer.name}'.")
            else:
                messages.info(request, "Lead já possui cliente vinculado.")

        return redirect("core:lead_detail", pk=pk)

    return render(request, "core/lead_detail.html", {
        "lead": lead,
        "interactions": interactions,
        "stages": LeadStage.choices,
        "channels": LeadInteraction.CHANNEL_CHOICES,
    })


@login_required
@require_http_methods(["POST"])
def lead_update_stage_api(request, pk):
    """AJAX endpoint for drag-and-drop stage change."""
    lead = get_object_or_404(Lead, pk=pk)
    new_stage = request.POST.get("stage", "")
    if new_stage in dict(LeadStage.choices):
        lead.stage = new_stage
        lead.save(update_fields=["stage", "updated_at"])
        return JsonResponse({"ok": True, "stage": new_stage})
    return JsonResponse({"ok": False, "error": "Estágio inválido"}, status=400)


# ──────────────────────────────────────────────────────────────────────
# Communication History
# ──────────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["POST"])
def add_communication(request):
    customer_id = request.POST.get("customer_id")
    quote_id = request.POST.get("quote_id") or None
    customer = get_object_or_404(Customer, pk=customer_id)

    CommunicationHistory.objects.create(
        customer=customer,
        quote_id=quote_id,
        channel=request.POST.get("channel", "OTHER"),
        summary=request.POST.get("summary", ""),
        next_steps=request.POST.get("next_steps", ""),
        created_by=request.user,
    )
    messages.success(request, "Comunicação registrada.")

    redirect_url = request.POST.get("redirect_url", "/dashboard/")
    return redirect(redirect_url)


# ──────────────────────────────────────────────────────────────────────
# Reports
# ──────────────────────────────────────────────────────────────────────
@login_required
def reports_hub(request):
    return render(request, "core/reports_hub.html")


@login_required
def report_sales(request):
    today = timezone.localdate()
    date_from = request.GET.get("date_from", str(today.replace(day=1)))
    date_to = request.GET.get("date_to", str(today))
    seller_id = request.GET.get("seller", "")

    qs = Quote.objects.filter(
        status=QuoteStatus.CONVERTED,
        quote_date__gte=date_from,
        quote_date__lte=date_to,
    ).select_related("customer", "seller")

    if seller_id:
        qs = qs.filter(seller_id=seller_id)

    total = qs.aggregate(total=Sum("total_value_snapshot"))["total"] or 0
    count = qs.count()
    avg_value = round(total / count, 2) if count > 0 else 0

    sellers = User.objects.filter(is_active=True).order_by("username")

    return render(request, "core/report_sales.html", {
        "quotes": qs.order_by("-quote_date"),
        "total": total,
        "count": count,
        "avg_value": avg_value,
        "date_from": date_from,
        "date_to": date_to,
        "seller_id": seller_id,
        "sellers": sellers,
    })


@login_required
def report_commissions(request):
    today = timezone.localdate()
    date_from = request.GET.get("date_from", str(today.replace(day=1)))
    date_to = request.GET.get("date_to", str(today))

    from core.models import SalesMarginConfig
    TOTAL_MARGIN, MIN_COMM, MAX_COMM = SalesMarginConfig.get_config()

    qs = Quote.objects.filter(
        status=QuoteStatus.CONVERTED,
        quote_date__gte=date_from,
        quote_date__lte=date_to,
    ).select_related("seller")

    commissions = []
    sellers_data = (
        qs.values("seller__id", "seller__username")
        .annotate(
            total_sold=Sum("total_value_snapshot"),
            count=Count("id"),
            avg_discount=Avg("discount_percent"),
            avg_fee=Avg("payment_fee_percent"),
        )
        .order_by("-total_sold")
    )

    for s in sellers_data:
        avg_disc = s["avg_discount"] or 0
        avg_fee = s["avg_fee"] or 0
        comm_pct = max(float(MIN_COMM), min(float(MAX_COMM), float(TOTAL_MARGIN) - avg_disc - avg_fee))
        total_sold = float(s["total_sold"] or 0)
        est_commission = total_sold * comm_pct / 100
        commissions.append({
            "seller": s["seller__username"],
            "total_sold": s["total_sold"],
            "count": s["count"],
            "avg_discount": round(avg_disc, 1),
            "comm_pct": round(comm_pct, 1),
            "est_commission": round(est_commission, 2),
        })

    return render(request, "core/report_commissions.html", {
        "commissions": commissions,
        "date_from": date_from,
        "date_to": date_to,
    })


@login_required
def report_discounts(request):
    today = timezone.localdate()
    date_from = request.GET.get("date_from", str(today.replace(day=1)))
    date_to = request.GET.get("date_to", str(today))

    qs = Quote.objects.filter(
        quote_date__gte=date_from,
        quote_date__lte=date_to,
        discount_percent__gt=0,
    ).select_related("seller", "customer", "discount_authorized_by").order_by("-discount_percent")

    avg = qs.aggregate(avg=Avg("discount_percent"))["avg"] or 0
    authorized_count = qs.filter(discount_authorized_by__isnull=False).count()

    return render(request, "core/report_discounts.html", {
        "quotes": qs,
        "avg_discount": round(avg, 1),
        "authorized_count": authorized_count,
        "date_from": date_from,
        "date_to": date_to,
    })


@login_required
def report_products(request):
    today = timezone.localdate()
    date_from = request.GET.get("date_from", str(today.replace(day=1)))
    date_to = request.GET.get("date_to", str(today))

    items = (
        QuoteItem.objects.filter(
            quote__status=QuoteStatus.CONVERTED,
            quote__quote_date__gte=date_from,
            quote__quote_date__lte=date_to,
        )
        .values("product_name")
        .annotate(
            qty=Sum("quantity"),
            total_value=Sum(F("quantity") * F("unit_value")),
        )
        .order_by("-total_value")[:50]
    )

    return render(request, "core/report_products.html", {
        "items": items,
        "date_from": date_from,
        "date_to": date_to,
    })


@login_required
def report_csv_export(request):
    import csv
    today = timezone.localdate()
    date_from = request.GET.get("date_from", str(today.replace(day=1)))
    date_to = request.GET.get("date_to", str(today))

    qs = Quote.objects.filter(
        status=QuoteStatus.CONVERTED,
        quote_date__gte=date_from,
        quote_date__lte=date_to,
    ).select_related("customer", "seller").order_by("-quote_date")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="vendas_{date_from}_{date_to}.csv"'
    response.write('\ufeff')

    writer = csv.writer(response, delimiter=";")
    writer.writerow(["Número", "Data", "Cliente", "Vendedor", "Desconto %", "Total R$"])

    for q in qs:
        writer.writerow([
            q.number,
            q.quote_date.strftime("%d/%m/%Y"),
            q.customer.name,
            q.seller.username,
            f"{q.discount_percent}",
            f"{q.total_value_snapshot}",
        ])

    return response


# ──────────────────────────────────────────────────────────────────────
# Audit Log View
# ──────────────────────────────────────────────────────────────────────
@login_required
def audit_log_list(request):
    user = request.user
    is_admin = user.role in (Role.ADMIN, Role.OWNER) or user.is_superuser
    if not is_admin:
        messages.error(request, "Acesso negado.")
        return redirect("core:dashboard")

    action_filter = request.GET.get("action", "")
    qs = AuditLog.objects.select_related("user").all()
    if action_filter:
        qs = qs.filter(action=action_filter)

    return render(request, "core/audit_log.html", {
        "logs": qs[:200],
        "actions": AuditAction.choices,
        "action_filter": action_filter,
    })


# ──────────────────────────────────────────────────────────────────────
# Sales Goals Management
# ──────────────────────────────────────────────────────────────────────
@login_required
def goals_list(request):
    user = request.user
    is_admin = user.role in (Role.ADMIN, Role.OWNER) or user.is_superuser

    if is_admin:
        goals = SalesGoal.objects.select_related("seller").all()
    else:
        goals = SalesGoal.objects.filter(Q(seller=user) | Q(goal_type=GoalType.COLLECTIVE))

    return render(request, "core/goals_list.html", {
        "goals": goals,
        "is_admin": is_admin,
    })


@login_required
@require_http_methods(["POST"])
def goal_create(request):
    user = request.user
    is_admin = user.role in (Role.ADMIN, Role.OWNER) or user.is_superuser
    if not is_admin:
        messages.error(request, "Apenas administradores podem criar metas.")
        return redirect("core:goals_list")

    goal_type = request.POST.get("goal_type", "INDIVIDUAL")
    seller_id = request.POST.get("seller_id") or None

    SalesGoal.objects.create(
        goal_type=goal_type,
        seller_id=seller_id if goal_type == "INDIVIDUAL" else None,
        period=request.POST.get("period", "MONTHLY"),
        period_start=request.POST.get("period_start"),
        period_end=request.POST.get("period_end"),
        target_value=Decimal(request.POST.get("target_value", "0")),
        target_quantity=int(request.POST.get("target_quantity", "0")),
    )
    messages.success(request, "Meta criada.")
    return redirect("core:goals_list")
