from pathlib import Path

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .httpapi import ClientError

from .forms import ClassificationFormSet, LineFormSet, UploadForm
from .graph import run_step
from .mocks import backfill_mock_history, seed_mock_batch
from .models import AuditEvent, Batch, Invoice, InvoiceLine, VatRun
from .plan import workspace_plan
from .vat import RATES
from .vat3 import anomalies, box_rows, chart_months, period_date_from_label, vat3_boxes


def batch_list(request):
    form = UploadForm()
    if request.method == "POST" and request.POST.get("action") == "mock_batch":
        batch = seed_mock_batch(request.POST.get("period_label", ""))
        return redirect("batch_detail", pk=batch.pk)
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            batch = Batch.objects.create(period_label=form.cleaned_data["period_label"])
            for upload in request.FILES.getlist("files"):
                upload.name = Path(upload.name).name
                Invoice.objects.create(batch=batch, file=upload, original_name=upload.name)
            run_step(batch.id, "extract", mock=request.POST.get("action") == "mock")
            return redirect("batch_detail", pk=batch.pk)
    return render(
        request,
        "items/list.html",
        {"batches": Batch.objects.all(), "form": form, "plan": workspace_plan(None), "batch": None},
    )


def batch_detail(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    lines = _lines(batch)
    blocked = _extraction_blocked(batch, lines)
    if request.method == "POST" and batch.status == Batch.EXTRACTED:
        formset = LineFormSet(request.POST, queryset=lines)
        action = request.POST.get("action")
        if formset.is_valid():
            _save_line_edits(batch, formset, "edited")
            if action in ("accept", "mock_vat") and not _extraction_blocked(batch, _lines(batch)):
                AuditEvent.objects.create(
                    batch=batch,
                    kind="extraction_accepted",
                    payload={"mock": action == "mock_vat"},
                )
                batch.status = Batch.EXTRACTION_ACCEPTED
                batch.save(update_fields=["status"])
                try:
                    run_step(batch.id, "classify", mock=action == "mock_vat")
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
        {"batch": batch, "formset": formset, "blocked": blocked, "plan": workspace_plan(batch)},
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
            "plan": workspace_plan(batch),
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
    boxes = batch.vat_return.boxes or {}
    if "T1" not in boxes:
        boxes = vat3_boxes(_lines(batch))
    priors = _prior_returns(batch)
    prior_boxes = [(run.payload or {}).get("boxes") or {} for run in priors]
    flags = anomalies(boxes, prior_boxes)
    points = [(run.period_date, (run.payload or {}).get("boxes") or {}) for run in reversed(priors)]
    points.append((period_date_from_label(batch.period_label), boxes))
    return render(
        request,
        "items/return.html",
        {
            "batch": batch,
            "sections": sections,
            "events": batch.events.all(),
            "plan": workspace_plan(batch),
            "boxes": boxes,
            "box_rows": box_rows(boxes),
            "flags": flags,
            "prior_count": len(prior_boxes),
            "mock_priors": sum(1 for run in priors if run.mock),
            "months": chart_months(points),
            "runs": batch.runs.all(),
        },
    )


def dashboard(request):
    backfill_mock_history()
    runs = VatRun.objects.select_related("batch")[:40]
    returns = list(VatRun.objects.filter(kind=VatRun.RETURN).order_by("-period_date", "-created")[:12])
    points = [(run.period_date, (run.payload or {}).get("boxes") or {}) for run in reversed(returns)]
    latest = VatRun.objects.filter(kind=VatRun.RETURN).order_by("-created").first()
    latest_boxes = (latest.payload or {}).get("boxes") if latest else None
    return render(
        request,
        "items/dashboard.html",
        {
            "batch": None,
            "plan": workspace_plan(None, dashboard=True),
            "runs": runs,
            "months": chart_months(points),
            "latest": latest,
            "latest_boxes": latest_boxes or {},
            "counts": {
                "extract": VatRun.objects.filter(kind=VatRun.EXTRACT).count(),
                "classify": VatRun.objects.filter(kind=VatRun.CLASSIFY).count(),
                "return": VatRun.objects.filter(kind=VatRun.RETURN).count(),
            },
        },
    )


def _lines(batch):
    return InvoiceLine.objects.filter(invoice__batch=batch).select_related("invoice")


def _prior_returns(batch):
    if batch.invoices.filter(read_source="mock").exists():
        backfill_mock_history()
    return list(
        VatRun.objects.filter(kind=VatRun.RETURN).exclude(batch=batch).order_by("-period_date", "-created")[:12]
    )


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
