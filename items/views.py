from pathlib import Path

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .httpapi import ClientError

from .forms import ClassificationFormSet, LineFormSet, UploadForm
from .graph import run_step
from .models import AuditEvent, Batch, Invoice, InvoiceLine
from .vat import RATES


def batch_list(request):
    form = UploadForm()
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            batch = Batch.objects.create(period_label=form.cleaned_data["period_label"])
            for upload in request.FILES.getlist("files"):
                upload.name = Path(upload.name).name
                Invoice.objects.create(batch=batch, file=upload, original_name=upload.name)
            run_step(batch.id, "extract")
            return redirect("batch_detail", pk=batch.pk)
    return render(request, "items/list.html", {"batches": Batch.objects.all(), "form": form})


def batch_detail(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    lines = _lines(batch)
    blocked = _extraction_blocked(batch, lines)
    if request.method == "POST" and batch.status == Batch.EXTRACTED:
        formset = LineFormSet(request.POST, queryset=lines)
        action = request.POST.get("action")
        if formset.is_valid():
            _save_line_edits(batch, formset, "edited")
            if action == "accept" and not _extraction_blocked(batch, _lines(batch)):
                AuditEvent.objects.create(batch=batch, kind="extraction_accepted", payload={})
                batch.status = Batch.EXTRACTION_ACCEPTED
                batch.save(update_fields=["status"])
                try:
                    run_step(batch.id, "classify")
                except ClientError as exc:
                    batch.status = Batch.EXTRACTED
                    batch.save(update_fields=["status"])
                    messages.error(request, str(exc))
                    return redirect("batch_detail", pk=batch.pk)
                return redirect("batch_vat", pk=batch.pk)
            return redirect("batch_detail", pk=batch.pk)
    else:
        formset = LineFormSet(queryset=lines)
    return render(
        request,
        "items/batch.html",
        {"batch": batch, "formset": formset, "blocked": blocked},
    )


def batch_vat(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if batch.status == Batch.EXTRACTED:
        return redirect("batch_detail", pk=batch.pk)
    lines = _lines(batch)
    if request.method == "POST" and batch.status == Batch.CLASSIFIED:
        formset = ClassificationFormSet(request.POST, queryset=lines)
        action = request.POST.get("action")
        if formset.is_valid():
            _save_categories(batch, formset)
            fresh = _lines(batch)
            if action == "accept" and all(line.category in RATES for line in fresh):
                AuditEvent.objects.create(batch=batch, kind="classification_accepted", payload={})
                try:
                    run_step(batch.id, "return")
                except ClientError as exc:
                    messages.error(request, str(exc))
                    return redirect("batch_vat", pk=batch.pk)
                return redirect("batch_return", pk=batch.pk)
            return redirect("batch_vat", pk=batch.pk)
    else:
        formset = ClassificationFormSet(queryset=lines)
    missing = any(line.category not in RATES for line in lines)
    return render(
        request,
        "items/vat.html",
        {
            "batch": batch,
            "formset": formset,
            "rows": list(zip(formset.forms, lines)),
            "missing_category": missing,
        },
    )


def batch_return(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if not hasattr(batch, "vat_return"):
        if batch.status == Batch.CLASSIFIED:
            return redirect("batch_vat", pk=batch.pk)
        return redirect("batch_detail", pk=batch.pk)
    sections = []
    for category, section in batch.vat_return.totals.items():
        sections.append(section)
    return render(
        request,
        "items/return.html",
        {
            "batch": batch,
            "sections": sections,
            "events": batch.events.all(),
        },
    )


def _lines(batch):
    return InvoiceLine.objects.filter(invoice__batch=batch).select_related("invoice")


def _extraction_blocked(batch, lines):
    return batch.invoices.exclude(error="").exists() or not lines.exists()


def _save_line_edits(batch, formset, kind):
    for form in formset.forms:
        for field in form.changed_data:
            old = form.initial.get(field, "")
            new = form.cleaned_data[field]
            if str(old) != str(new):
                AuditEvent.objects.create(
                    batch=batch,
                    kind=kind,
                    payload={
                        "line_id": form.instance.pk,
                        "field": field,
                        "old": "" if old is None else str(old),
                        "new": "" if new is None else str(new),
                    },
                )
        form.save()


def _save_categories(batch, formset):
    for form in formset.forms:
        if "category" not in form.changed_data:
            form.save()
            continue
        old = form.initial.get("category", "")
        new = form.cleaned_data["category"]
        line = form.save(commit=False)
        line.rate = RATES.get(new, "")
        if new in RATES:
            line.classification_error = ""
        line.save()
        if str(old) != str(new):
            AuditEvent.objects.create(
                batch=batch,
                kind="classification_edited",
                payload={"line_id": line.pk, "field": "category", "old": old, "new": new},
            )
