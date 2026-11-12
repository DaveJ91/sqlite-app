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


class ClassificationForm(forms.ModelForm):
    category = forms.ChoiceField(choices=[("", "Choose")] + [(key, label) for key, label in LABELS.items()])

    class Meta:
        model = InvoiceLine
        fields = ["category"]


LineFormSet = modelformset_factory(InvoiceLine, form=LineForm, extra=0)
ClassificationFormSet = modelformset_factory(
    InvoiceLine,
    form=ClassificationForm,
    extra=0,
)
