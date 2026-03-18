import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django; django.setup()
from decimal import Decimal
from sales.views import _build_simulation_context

def t(label, **kw):
    defaults = dict(subtotal=Decimal('10000'), freight_value=Decimal('0'),
                    sim_payment_type='CASH', sim_has_architect=False,
                    sim_discount=Decimal('0'), price_increase_pct=Decimal('0'),
                    sim_installments=1)
    defaults.update(kw)
    ctx = _build_simulation_context(**defaults)
    print(f"  {label}")
    print(f"    input_disc={defaults['sim_discount']}% → applied_disc={ctx['discount_percent']}%")
    print(f"    max_disc_allowed={ctx['max_discount_allowed']}% | max_inst={ctx['max_installments_allowed']}")
    print(f"    com={ctx['seller_commission_percent']}% | total_cost={ctx['total_cost_pct']}% | margin={ctx['effective_margin']}%")
    print(f"    room={ctx['effective_margin'] - (ctx['store_fee_percent'] + Decimal(str(ctx.get('architect_cost_pct', 0) if 'architect_cost_pct' in ctx else 0)) + ctx['discount_percent'])}%")
    print(f"    fee={ctx['store_fee_percent']}% | arch={ctx['architect_percent']}% | blocked={ctx['controls_blocked']}")
    print()

print("=== Reproduce user scenario: cost=7%, margin=10%, can't go above 2% ===\n")

# Scenario: 1x cash, 2% discount → cost should be 7% (fee=0 + disc=2 + com=5 = 7)
t("1x cash, 2% disc (should show 7% cost, slider max=13)", sim_discount=Decimal('2'))
# Now try to increase discount to 5%
t("1x cash, 5% disc (should work!)", sim_discount=Decimal('5'))
t("1x cash, 10% disc (should work!)", sim_discount=Decimal('10'))
t("1x cash, 13% disc (max)", sim_discount=Decimal('13'))
t("1x cash, 15% disc (should clamp to 13)", sim_discount=Decimal('15'))

print("=== 2x CC scenarios ===\n")
t("2x CC, 0% disc", sim_payment_type='CREDIT_CARD', sim_installments=2)
t("2x CC, 5% disc", sim_payment_type='CREDIT_CARD', sim_installments=2, sim_discount=Decimal('5'))

print("=== Check ArchitectCommission value ===\n")
from core.models import ArchitectCommission
print(f"  ArchitectCommission.get_commission() = {ArchitectCommission.get_commission()}%")

print("\n=== With architect ===\n")
t("1x cash + architect, 0% disc", sim_has_architect=True)
t("1x cash + architect, 3% disc (max)", sim_has_architect=True, sim_discount=Decimal('3'))
t("1x cash + architect, 5% disc (should clamp)", sim_has_architect=True, sim_discount=Decimal('5'))

print("=== Check all fees ===\n")
from core.models import PaymentTariff
for n in range(1, 19):
    fee = PaymentTariff.get_fee('CREDIT_CARD', n)
    print(f"  CC {n}x: {fee}%")
