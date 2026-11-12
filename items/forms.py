from pathlib import Path

from django import forms
from django.forms import modelformset_factory

from .models import InvoiceLine
from .vat import LABELS

MAX_INVOICES = 10


class UploadForm(forms.Form):
    period_label = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={"placeholder": "Period, for example March 2026"}),
    )

    def clean(self):
        cleaned = super().clean()
        files = self.files.getlist("files")
        if not files:
            raise forms.ValidationError("Choose a folder or up to 10 PDF invoices.")
        if len(files) > MAX_INVOICES:
            raise forms.ValidationError("Upload 10 invoices or fewer.")
        for upload in files:
            name = Path(upload.name).name
            if not name.lower().endswith(".pdf"):
                raise forms.ValidationError(f"{name} is not a PDF.")
            header = upload.read(5)
            upload.seek(0)
            if header != b"%PDF-":
                raise forms.ValidationError(f"{name} is not a PDF.")
        return cleaned


class LineForm(forms.ModelForm):
    class Meta:
        model = InvoiceLine
        fields = [
            "supplier",
            "invoice_number",
            "invoice_date",
            "currency",
            "description",
            "net_amount",
            "vat_amount",
            "gross_amount",
        ]
        widgets = {
            "description": forms.TextInput(),
        }


class ClassificationForm(forms.ModelForm):
    category = forms.ChoiceField(choices=[("", "Choose")] + [(key, label) for key, label in LABELS.items()])
    supply = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Supply"),
            ("taxable", "Taxable supply"),
            ("exempt", "Exempt"),
            ("outside", "Outside the scope"),
            ("none", "No supply"),
        ],
    )
    nature = forms.ChoiceField(
        required=False,
        choices=[("", "Good or service"), ("good", "Good"), ("service", "Service")],
    )
    place_of_supply = forms.ChoiceField(
        required=False,
        choices=[("", "Place"), ("ireland", "Ireland"), ("eu", "Other EU"), ("non_eu", "Outside the EU")],
    )
    who_accounts = forms.ChoiceField(
        required=False,
        choices=[("", "Who accounts"), ("supplier", "Supplier"), ("customer", "Customer"), ("none", "No VAT")],
    )

    class Meta:
        model = InvoiceLine
        fields = ["category", "supply", "nature", "place_of_supply", "who_accounts"]


class DeductibilityForm(forms.ModelForm):
    deductible = forms.ChoiceField(
        choices=[
            ("", "Choose"),
            ("full", "Fully deductible"),
            ("partial", "Partial"),
            ("none", "Non-deductible"),
        ]
    )
    blocked_reason = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Blocked input"),
            ("none", "Not blocked"),
            ("food", "Food or drink"),
            ("accommodation", "Accommodation"),
            ("entertainment", "Entertainment"),
            ("personal", "Other personal services"),
            ("petrol", "Petrol"),
            ("passenger_car", "Passenger car"),
            ("exempt_use", "Exempt use"),
            ("non_business", "Non-business"),
            ("no_vat", "No VAT on the invoice"),
        ],
    )
    deductible_percent = forms.CharField(required=False)

    class Meta:
        model = InvoiceLine
        fields = ["deductible", "blocked_reason", "deductible_percent"]

    def clean_deductible_percent(self):
        raw = (self.cleaned_data.get("deductible_percent") or "").strip()
        if not raw:
            return ""
        from decimal import Decimal

        from .vat import parse_amount

        amount = parse_amount(raw)
        if amount < 0 or amount > Decimal("100"):
            raise forms.ValidationError("Use a percentage from 0 to 100.")
        if amount == amount.to_integral():
            return str(int(amount))
        return f"{amount.quantize(Decimal('0.01'))}"


LineFormSet = modelformset_factory(InvoiceLine, form=LineForm, extra=0)
ClassificationFormSet = modelformset_factory(InvoiceLine, form=ClassificationForm, extra=0)
DeductibilityFormSet = modelformset_factory(InvoiceLine, form=DeductibilityForm, extra=0)
