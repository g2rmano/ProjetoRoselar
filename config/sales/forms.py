from django import forms
from django.forms import inlineformset_factory

from .models import Quote, QuoteItem, QuoteItemImage
from core.models import PaymentMethodType


class QuoteForm(forms.ModelForm):
    # Add payment_type as a choice field
    payment_type = forms.ChoiceField(
        choices=[('', '--- Selecione ---')] + list(PaymentMethodType.choices),
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        label="Método de Pagamento"
    )
    
    class Meta:
        model = Quote
        fields = [
            "number",
            "customer",
            "quote_date",
            "delivery_deadline",
            "freight_value",
            "freight_responsible",
            "shipping_company",
            "shipping_payment_method",
            "discount_percent",
            "has_architect",
            "payment_type",
            "payment_installments",
            "payment_fee_percent",
        ]
        widgets = {
            "quote_date": forms.DateInput(attrs={"type": "date"}),
            "delivery_deadline": forms.DateInput(attrs={"type": "date"}),
            "number": forms.TextInput(attrs={"readonly": "readonly"}),
            "payment_installments": forms.Select(attrs={"class": "form-control"}),
            "payment_fee_percent": forms.HiddenInput(),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Pricing fields are optional in Step 1 (set in Step 2 – pricing page)
        self.fields['discount_percent'].required = False
        self.fields['has_architect'].required = False
        self.fields['payment_installments'].required = False
        self.fields['payment_fee_percent'].required = False
        
        # Adicionar classes CSS
        for field_name, field in self.fields.items():
            if field_name not in ['number', 'payment_fee_percent']:
                if 'class' not in field.widget.attrs:
                    field.widget.attrs['class'] = 'form-control'


class QuoteItemForm(forms.ModelForm):
    # Make architect_percent not required and hidden by default
    architect_percent = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
        label="% Arquiteto"
    )
    
    # Image upload for the item (shown in buyer's PDF)
    item_image = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        label="Imagem do Produto"
    )
    
    class Meta:
        model = QuoteItem
        fields = [
            "supplier",
            "product_name",
            "description",
            "quantity",
            "unit_value",
            "architect_percent",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 1}),
        }


QuoteItemFormSet = inlineformset_factory(
    Quote,
    QuoteItem,
    form=QuoteItemForm,
    extra=1,
    can_delete=True,
)
