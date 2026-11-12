from django.shortcuts import get_object_or_404, redirect, render

from .forms import InvoiceForm
from .models import Invoice
from .vat import ClassificationError, classify_invoice, extract_text


def invoice_list(request):
    form = InvoiceForm()
    if request.method == "POST":
        form = InvoiceForm(request.POST, request.FILES)
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.original_name = request.FILES["file"].name
            try:
                invoice.extracted_text = extract_text(invoice.file)
            except ClassificationError as exc:
                invoice.error = str(exc)
            else:
                if not invoice.extracted_text:
                    invoice.error = (
                        "Could not read any text from this PDF. "
                        "Scanned invoices need a text layer."
                    )
                else:
                    try:
                        invoice.classification = classify_invoice(
                            invoice.extracted_text
                        )
                    except ClassificationError as exc:
                        invoice.error = str(exc)
            invoice.save()
            return redirect("invoice_detail", pk=invoice.pk)
    return render(
        request,
        "items/list.html",
        {"invoices": Invoice.objects.all(), "form": form},
    )


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    return render(request, "items/detail.html", {"invoice": invoice})
