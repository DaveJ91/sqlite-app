from django import forms

from .models import Invoice


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["file"]

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Upload a PDF invoice.")
        if upload.size > 10 * 1024 * 1024:
            raise forms.ValidationError("PDF must be under 10 MB.")
        header = upload.read(5)
        upload.seek(0)
        if header != b"%PDF-":
            raise forms.ValidationError("That file is not a PDF.")
        return upload
