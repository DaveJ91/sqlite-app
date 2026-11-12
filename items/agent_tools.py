"""Read and step operations shared by the desk and, later, the MCP server.

Amounts come from the database. A preview changes nothing on the line.
"""

from .flags import batch_flags
from .graph import run_step
from .mocks import backfill_mock_history
from .models import Batch, InvoiceLine, VatRun
from .training import DESCRIPTION_TREATMENT, treatment_for
from .vat import LABELS, RATES, format_amount
from .vat3 import (
    anomalies,
    box_rows,
    chart_months,
    period_date_from_label,
    recovery_from_decision,
    vat3_boxes,
)


def lines_for(batch):
    return list(InvoiceLine.objects.filter(invoice__batch=batch).select_related("invoice"))


def list_lines(batch):
    flags = {}
    for item in batch_flags(lines_for(batch)):
        flags.setdefault(item["line_id"], []).append(item["text"])
    rows = []
    for line in lines_for(batch):
        rows.append(
            {
                "id": line.id,
                "description": line.description,
                "supplier": line.supplier,
                "invoice_number": line.invoice_number,
                "net": line.net_amount,
                "vat": line.vat_amount,
                "gross": line.gross_amount,
                "category": line.category,
                "rate": line.rate,
                "deductible": line.deductible,
                "blocked_reason": line.blocked_reason,
                "deductible_percent": line.deductible_percent,
                "deductible_vat": line.deductible_vat,
                "flags": flags.get(line.id, []),
                "classification_steps": [step.get("step", "") for step in line.classification_chain or []],
                "deductibility_steps": [step.get("step", "") for step in line.deductibility_chain or []],
            }
        )
    return {"status": batch.status, "lines": rows}


def get_return(batch):
    lines = lines_for(batch)
    boxes = vat3_boxes(lines)
    return {"boxes": boxes, "rows": box_rows(boxes)}


def preview_return(batch, overrides):
    """Apply deductibility overrides in memory and return the boxes."""
    lines = lines_for(batch)
    by_id = {line.id: line for line in lines}
    applied = []
    for change in overrides or []:
        if not isinstance(change, dict):
            continue
        try:
            line_id = int(change.get("line_id"))
        except (TypeError, ValueError):
            continue
        line = by_id.get(line_id)
        if line is None:
            continue
        for field in ("deductible", "deductible_percent", "blocked_reason", "category"):
            if change.get(field) not in (None, ""):
                setattr(line, field, str(change[field]))
        if line.deductible in ("full", "partial", "none"):
            if line.deductible == "none":
                line.deductible_percent = "0"
            elif line.deductible == "full" and not line.deductible_percent:
                line.deductible_percent = "100"
            line.deductible_vat = format_amount(recovery_from_decision(line))
        applied.append(line_id)
    boxes = vat3_boxes(lines)
    return {"boxes": boxes, "rows": box_rows(boxes), "applied": applied, "saved": False}


def lookup_treatment(description):
    known = description in DESCRIPTION_TREATMENT
    spec = treatment_for(description)
    return {
        "description": description,
        "known": known,
        "category": spec["category"],
        "label": LABELS.get(spec["category"], spec["category"]),
        "deductible": spec["deductible"],
        "blocked_reason": spec["blocked_reason"],
        "reason": spec["reason"],
        "deduct_reason": spec["deduct_reason"],
        "class_chain": spec["class_chain"],
        "deduct_chain": spec["deduct_chain"],
    }


def list_audit(batch):
    events = [
        {"kind": event.kind, "payload": event.payload}
        for event in batch.events.all()[:40]
    ]
    return {"events": events}


def assess_deductibility(batch, mock=True):
    if batch.status != Batch.CLASSIFIED:
        return {"error": "Deductibility runs after the classification review."}
    lines = lines_for(batch)
    if not lines or any(line.category not in RATES for line in lines):
        return {"error": "Every line needs a VAT category first."}
    run_step(batch.id, "deduct", mock=bool(mock))
    batch.refresh_from_db()
    return {"status": batch.status, "mock": bool(mock), "saved": True}


def comparison_for(batch, boxes):
    if batch.invoices.filter(read_source="mock").exists():
        backfill_mock_history()
    priors = list(
        VatRun.objects.filter(kind=VatRun.RETURN).exclude(batch=batch).order_by("-period_date", "-created")[:12]
    )
    prior_boxes = [(run.payload or {}).get("boxes") or {} for run in priors]
    found = anomalies(boxes, prior_boxes)
    points = [(run.period_date, (run.payload or {}).get("boxes") or {}) for run in reversed(priors)]
    points.append((period_date_from_label(batch.period_label), boxes))
    return {
        "months": chart_months(points),
        "texts": [flag["text"] for flag in found],
        "prior_count": len(priors),
    }


def batch_status_label(batch):
    return dict(Batch.STATUS_CHOICES).get(batch.status, batch.status)
