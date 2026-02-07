from django import forms
from django.forms import inlineformset_factory

from .models import Quote, QuoteItem, QuoteItemImage


class QuoteForm(forms.ModelForm):
    class Meta:
        model = Quote
        fields = [
            "number",
            "customer",
            "quote_date",
            "freight_value",
            "discount_percent",
            "payment_description",
        ]


class QuoteItemForm(forms.ModelForm):
    class Meta:
        model = QuoteItem
        fields = [
            "supplier",
            "product_name",
            "description",
            "quantity",
            "unit_value",
            "condition_text",
            "architect_percent",
        ]


QuoteItemFormSet = inlineformset_factory(
    Quote,
    QuoteItem,
    form=QuoteItemForm,
    extra=1,
    can_delete=True,
)
