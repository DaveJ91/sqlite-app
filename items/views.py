from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .httpapi import ClientError

from .desk import ask, present
from .flags import batch_flags
from .forms import ClassificationForm, ClassificationFormSet, DeductibilityForm, DeductibilityFormSet, LineForm, LineFormSet, UploadForm
from .graph import run_step
from .mocks import backfill_mock_history, seed_mock_batch
from .models import AuditEvent, Batch, Invoice, InvoiceLine, SignOff, VatRun
from .plan import workspace_plan
from .vat import LABELS, RATES, format_amount
from .vat3 import anomalies, box_rows, chart_months, period_date_from_label, recovery_from_decision, vat3_boxes


def batch_list(request):
    form = UploadForm()
    action = request.POST.get("action")
    if request.method == "POST" and action == "mock_batch":
        batch = seed_mock_batch(request.POST.get("period_label", ""))
        return redirect("batch_detail", pk=batch.pk)
    if request.method == "POST" and action in ("ask", "ask_file"):
        return _desk_from_home(request)
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


def batch_desk(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "apply_edit":
            _apply_desk_edit(request, batch)
        elif action == "run":
            _run_from_desk(request, batch)
        else:
            question = (request.POST.get("question") or "").strip()
            if not question:
                messages.error(request, "Ask a question first.")
            elif action == "ask" and not (settings.XAI_API_KEY or "").strip():
                messages.error(request, "Set XAI_API_KEY, or answer from the file.")
            else:
                ask(batch, question, use_grok=action == "ask")
        return redirect("batch_desk", pk=batch.pk)
    turns = [
        {"question": turn.question, "mock": turn.mock, "created": turn.created, "blocks": present(batch, turn.blocks)}
        for turn in batch.desk_turns.all()
    ]
    return render(
        request,
        "items/desk.html",
        {"batch": batch, "plan": workspace_plan(batch), "turns": turns},
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
            fresh = list(_lines(batch))
            if action in ("accept", "mock_deduct") and all(line.category in RATES for line in fresh):
                AuditEvent.objects.create(
                    batch=batch,
                    kind="classification_accepted",
                    payload={"mock_deductibility": action == "mock_deduct", "overrides": _override_count(fresh, "classification")},
                )
                try:
                    run_step(batch.id, "deduct", mock=action == "mock_deduct")
                except ClientError as exc:
                    messages.error(request, str(exc))
                    return redirect("batch_vat", pk=batch.pk)
                return redirect("batch_deduct", pk=batch.pk)
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
            "flags": batch_flags(lines),
            "plan": workspace_plan(batch),
        },
    )


def batch_deduct(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if batch.status in (Batch.EXTRACTED, Batch.EXTRACTION_ACCEPTED):
        return redirect("batch_detail", pk=batch.pk)
    if batch.status == Batch.CLASSIFIED:
        return redirect("batch_vat", pk=batch.pk)
    lines = _lines(batch)
    if request.method == "POST" and batch.status == Batch.DEDUCTIBILITY:
        formset = DeductibilityFormSet(request.POST, queryset=lines)
        action = request.POST.get("action")
        if formset.is_valid():
            _save_deductibility(batch, formset)
            fresh = list(_lines(batch))
            if action == "accept" and not _deduct_incomplete(fresh):
                AuditEvent.objects.create(
                    batch=batch,
                    kind="deduction_accepted",
                    payload={"overrides": _override_count(fresh, "deductibility"), "non_deductible": sum(1 for line in fresh if line.deductible == "none")},
                )
                batch.status = Batch.DEDUCTION_ACCEPTED
                batch.save(update_fields=["status"])
                return redirect("batch_signoff", pk=batch.pk)
            return redirect("batch_deduct", pk=batch.pk)
    else:
        formset = DeductibilityFormSet(queryset=lines)
    return render(
        request,
        "items/deduct.html",
        {
            "batch": batch,
            "formset": formset,
            "rows": list(zip(formset.forms, lines)),
            "incomplete": _deduct_incomplete(lines),
            "flags": batch_flags(lines),
            "plan": workspace_plan(batch),
        },
    )


def batch_signoff(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if batch.status == Batch.DEDUCTIBILITY:
        return redirect("batch_deduct", pk=batch.pk)
    if batch.status in (Batch.EXTRACTED, Batch.EXTRACTION_ACCEPTED, Batch.CLASSIFIED):
        return _redirect_for_status(batch)
    lines = list(_lines(batch))
    flags = batch_flags(lines)
    evidence = _evidence(batch, lines)
    error = ""
    if request.method == "POST" and batch.status == Batch.DEDUCTION_ACCEPTED:
        reviewer = (request.POST.get("reviewer") or "").strip()
        note = (request.POST.get("note") or "").strip()
        acknowledged = request.POST.get("acknowledge") == "on"
        if not reviewer:
            error = "Sign with your name. That name is the review record."
        elif flags and not acknowledged:
            error = "Tick the box once you have looked at every flag."
        else:
            sign_off, _created = SignOff.objects.update_or_create(
                batch=batch,
                defaults={
                    "reviewer": reviewer,
                    "note": note,
                    "acknowledged": acknowledged or not flags,
                    "flag_count": len(flags),
                    "flags": flags,
                    "extraction_edits": evidence["extraction_edits"],
                    "classification_overrides": evidence["classification_overrides"],
                    "deductibility_overrides": evidence["deductibility_overrides"],
                },
            )
            AuditEvent.objects.create(
                batch=batch,
                kind="signed_off",
                payload={
                    "reviewer": reviewer,
                    "note": note,
                    "flag_count": len(flags),
                    "classification_overrides": evidence["classification_overrides"],
                    "deductibility_overrides": evidence["deductibility_overrides"],
                    "sign_off_id": sign_off.id,
                },
            )
            run_step(batch.id, "return")
            return redirect("batch_return", pk=batch.pk)
    return render(
        request,
        "items/signoff.html",
        {
            "batch": batch,
            "flags": flags,
            "evidence": evidence,
            "error": error,
            "plan": workspace_plan(batch),
            "sign_off": getattr(batch, "sign_off", None) if batch.status == Batch.RETURN_READY else None,
        },
    )


def batch_return(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if not hasattr(batch, "vat_return"):
        return _redirect_for_status(batch)
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
            "sign_off": getattr(batch, "sign_off", None),
            "evidence": _evidence(batch, list(_lines(batch))),
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


def _desk_from_home(request):
    action = request.POST.get("action")
    question = (request.POST.get("question") or "").strip() or "What should I look at on this batch?"
    if action == "ask" and not (settings.XAI_API_KEY or "").strip():
        messages.error(request, "Set XAI_API_KEY, or answer from the file.")
        return redirect("batch_list")
    batch = Batch.objects.order_by("-id").first()
    if batch is None:
        batch = seed_mock_batch(request.POST.get("period_label", ""))
    ask(batch, question, use_grok=action == "ask")
    return redirect("batch_desk", pk=batch.pk)


def _run_from_desk(request, batch):
    if request.POST.get("step") != "deduct" or batch.status != Batch.CLASSIFIED:
        messages.error(request, "That step is not open.")
        return
    try:
        run_step(batch.id, "deduct", mock=True)
    except ClientError as exc:
        messages.error(request, str(exc))
        return
    ask(batch, "Run mock deductibility", use_grok=False)


def _apply_desk_edit(request, batch):
    try:
        line_id = int(request.POST.get("line_id") or 0)
    except ValueError:
        messages.error(request, "That line is not on this batch.")
        return
    lines = list(_lines(batch))
    if not any(line.id == line_id for line in lines):
        messages.error(request, "That line is not on this batch.")
        return
    form_class, formset_class, save = {
        Batch.EXTRACTED: (LineForm, LineFormSet, lambda formset: _save_line_edits(batch, formset, "edited")),
        Batch.CLASSIFIED: (ClassificationForm, ClassificationFormSet, lambda formset: _save_categories(batch, formset)),
        Batch.DEDUCTIBILITY: (DeductibilityForm, DeductibilityFormSet, lambda formset: _save_deductibility(batch, formset)),
    }.get(batch.status, (None, None, None))
    if form_class is None:
        messages.error(request, "That edit is closed on this batch.")
        return
    data = {
        "form-TOTAL_FORMS": str(len(lines)),
        "form-INITIAL_FORMS": str(len(lines)),
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
    }
    fields = list(form_class.base_fields)
    for index, line in enumerate(lines):
        data[f"form-{index}-id"] = str(line.id)
        for field in fields:
            if line.id == line_id and field in request.POST:
                data[f"form-{index}-{field}"] = request.POST.get(field, "")
            else:
                value = getattr(line, field)
                data[f"form-{index}-{field}"] = "" if value is None else str(value)
    formset = formset_class(data, queryset=_lines(batch))
    if not formset.is_valid():
        messages.error(request, "That edit was not saved.")
        return
    save(formset)


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
        _audit_changes(batch, form, "classification_edited")
        line = form.save(commit=False)
        line.rate = RATES.get(line.category, "")
        if line.category in RATES:
            line.classification_error = ""
        line.save()


def _save_deductibility(batch, formset):
    for form in formset.forms:
        _audit_changes(batch, form, "deduction_edited")
        line = form.save(commit=False)
        if line.deductible == "full" and not line.deductible_percent:
            line.deductible_percent = "100"
        if line.deductible == "none":
            line.deductible_percent = "0"
        if line.deductible in ("full", "partial", "none"):
            line.deductibility_error = ""
            line.deductible_vat = format_amount(recovery_from_decision(line))
        line.save()


def _audit_changes(batch, form, kind):
    for field in form.changed_data:
        old = form.initial.get(field, "")
        new = form.cleaned_data[field]
        if str(old or "") != str(new or ""):
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


def _deduct_incomplete(lines):
    for line in lines:
        if line.deductible not in ("full", "partial", "none"):
            return True
        if line.deductible == "partial" and not (line.deductible_percent or "").strip():
            return True
    return False


def _override_count(lines, kind):
    if kind == "classification":
        return sum(1 for line in lines if line.classification_overridden)
    return sum(1 for line in lines if line.deductibility_overridden)


def _evidence(batch, lines):
    edits = batch.events.filter(kind="edited").count()
    return {
        "extraction_edits": edits,
        "classification_overrides": _override_count(lines, "classification"),
        "deductibility_overrides": _override_count(lines, "deductibility"),
        "overrides": [row for row in (_override_row(line) for line in lines) if row],
        "events": batch.events.all(),
    }


def _override_row(line):
    changes = []
    if line.model_category and line.model_category != line.category:
        changes.append(f"rate {LABELS.get(line.model_category, line.model_category)} → {LABELS.get(line.category, line.category)}")
    if line.model_supply and line.model_supply != line.supply:
        changes.append(f"supply {line.model_supply} → {line.supply}")
    if line.model_nature and line.model_nature != line.nature:
        changes.append(f"nature {line.model_nature} → {line.nature}")
    if line.model_place and line.model_place != line.place_of_supply:
        changes.append(f"place {line.model_place} → {line.place_of_supply}")
    if line.model_who and line.model_who != line.who_accounts:
        changes.append(f"who accounts {line.model_who} → {line.who_accounts}")
    if line.model_deductible and line.model_deductible != line.deductible:
        changes.append(f"deductible {line.model_deductible} → {line.deductible}")
    if line.model_blocked and line.model_blocked != line.blocked_reason:
        changes.append(f"blocked input {line.model_blocked} → {line.blocked_reason}")
    if line.model_percent and line.model_percent != line.deductible_percent:
        changes.append(f"percent {line.model_percent} → {line.deductible_percent}")
    if not changes:
        return None
    return {"description": line.description, "changes": changes}


def _redirect_for_status(batch):
    if batch.status == Batch.DEDUCTION_ACCEPTED:
        return redirect("batch_signoff", pk=batch.pk)
    if batch.status == Batch.DEDUCTIBILITY:
        return redirect("batch_deduct", pk=batch.pk)
    if batch.status == Batch.CLASSIFIED:
        return redirect("batch_vat", pk=batch.pk)
    return redirect("batch_detail", pk=batch.pk)
