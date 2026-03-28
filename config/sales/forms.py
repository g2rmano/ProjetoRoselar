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
            "customer",
            "delivery_weeks",
            "freight_value",
            "freight_responsible",
            "shipping_company",
            "discount_percent",
            "has_architect",
            "payment_type",
            "payment_installments",
            "payment_fee_percent",
        ]
        widgets = {
            "freight_value": forms.TextInput(attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "0,00"}),
            "delivery_weeks": forms.Select(attrs={"class": "form-control"}),
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
        
        # Freight fields are conditionally required (validated in clean)
        self.fields['freight_value'].required = False
        self.fields['delivery_weeks'].required = False
        self.fields['shipping_company'].required = False
        
        # Adicionar classes CSS
        for field_name, field in self.fields.items():
            if field_name not in ['payment_fee_percent']:
                if 'class' not in field.widget.attrs:
                    field.widget.attrs['class'] = 'form-control'

    def clean(self):
        cleaned = super().clean()
        responsible = cleaned.get('freight_responsible')
        from decimal import Decimal

        if responsible in ('STORE', 'CARRIER'):
            fv = cleaned.get('freight_value')
            if fv is None or fv <= Decimal('0'):
                self.add_error('freight_value', 'Informe o valor do frete.')
            if not cleaned.get('delivery_weeks'):
                self.add_error('delivery_weeks', 'Informe o prazo de entrega.')
            if responsible == 'CARRIER' and not cleaned.get('shipping_company'):
                self.add_error('shipping_company', 'Selecione a transportadora.')

        return cleaned


class QuoteItemForm(forms.ModelForm):
    # Make architect_percent not required and hidden by default
    architect_percent = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
        label="% Arquiteto"
    )
    
    # Override unit_value as CharField to accept Brazilian currency format
    unit_value = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'inputmode': 'numeric'}),
        label="Valor Unitário"
    )
    
    # Image upload for the item (shown in buyer's PDF)
    item_image = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        label="Imagem do Produto"
    )

    def clean_unit_value(self):
        raw = self.cleaned_data.get('unit_value', '')
        if not raw:
            raise forms.ValidationError('Informe o valor unitário.')
        # Accept both "1234.56" (JS-converted) and "1.234,56" (Brazilian format)
        raw = str(raw).strip()
        if ',' in raw:
            raw = raw.replace('.', '').replace(',', '.')
        from decimal import Decimal, InvalidOperation
        try:
            val = Decimal(raw)
        except InvalidOperation:
            raise forms.ValidationError('Valor unitário inválido.')
        if val <= 0:
            raise forms.ValidationError('O valor unitário deve ser maior que zero.')
        return val
    
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
