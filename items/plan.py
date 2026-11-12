from django.urls import reverse

from .models import InvoiceLine

STEPS = ("load", "review", "classify", "vat", "deduct", "deduct_review", "signoff", "return")

TITLES = {
    "load": "Load invoices",
    "review": "Review extraction",
    "classify": "Classify VAT",
    "vat": "Review classifications",
    "deduct": "Assess deductibility",
    "deduct_review": "Review non-deductible VAT",
    "signoff": "Sign off",
    "return": "Prepare the return",
}


def workspace_plan(batch, dashboard=False):
    if batch is None:
        done = set()
        current = "load"
        counts = {"invoices": 0, "lines": 0, "low": 0}
    else:
        done, current = _progress(batch.status)
        lines = InvoiceLine.objects.filter(invoice__batch=batch)
        counts = {
            "invoices": batch.invoices.count(),
            "lines": lines.count(),
            "low": sum(1 for line in lines if line.low_confidence),
        }
    details = {
        "load": _load_detail(batch, counts),
        "review": "Edit a cell if the reading is wrong",
        "classify": "Live API, or the labelled mock",
        "vat": _vat_detail(batch, counts),
        "deduct": "Live API, or the labelled mock",
        "deduct_review": _deduct_detail(batch, counts),
        "signoff": "Name, flags, and every override",
        "return": "VAT3 boxes, payment or refund, last 12 months",
    }
    steps = []
    for key in STEPS:
        if dashboard:
            state = "todo"
        elif current is None or (key in done and key != current):
            state = "done"
        elif key == current:
            state = "current"
        else:
            state = "todo"
        steps.append(
            {
                "key": key,
                "title": TITLES[key],
                "detail": details[key],
                "state": state,
                "href": _href(batch, key, done, current),
            }
        )
    if dashboard:
        kicker = "Overview"
        current_title = "Dashboard"
    elif current is None:
        kicker = "Complete"
        current_title = "VAT return"
    else:
        kicker = f"Step {STEPS.index(current) + 1} of {len(STEPS)}"
        current_title = TITLES[current]
    return {"steps": steps, "kicker": kicker, "current_title": current_title}


def _progress(status):
    if status == "return_ready":
        return set(STEPS), None
    if status == "deduction_accepted":
        return {"load", "review", "classify", "vat", "deduct", "deduct_review"}, "signoff"
    if status == "deductibility":
        return {"load", "review", "classify", "vat", "deduct"}, "deduct_review"
    if status == "classified":
        return {"load", "review", "classify"}, "vat"
    if status == "extraction_accepted":
        return {"load", "review"}, "classify"
    return {"load"}, "review"


def _load_detail(batch, counts):
    if batch is None:
        return "Upload PDFs, or start from 50 mock lines"
    return f"{counts['lines']} lines · {counts['invoices']} invoices"


def _vat_detail(batch, counts):
    if batch is None or batch.status not in ("classified", "deductibility", "deduction_accepted", "return_ready"):
        return "Chain of supply, place, and rate"
    if counts["low"]:
        return f"{counts['low']} low-confidence lines"
    return "Every line is above the threshold"


def _deduct_detail(batch, counts):
    if batch is None or batch.status not in ("deductibility", "deduction_accepted", "return_ready"):
        return "Blocked inputs stay out of T2"
    blocked = InvoiceLine.objects.filter(invoice__batch=batch, deductible="none").count()
    if blocked:
        return f"{blocked} non-deductible lines"
    return "Nothing blocked"


def _href(batch, key, done, current):
    if batch is None or (key not in done and key != current):
        return ""
    if key == "load":
        return reverse("batch_list")
    if key in ("review", "classify"):
        return reverse("batch_detail", args=[batch.pk])
    if key == "vat" and batch.status in ("classified", "deductibility", "deduction_accepted", "return_ready"):
        return reverse("batch_vat", args=[batch.pk])
    if key == "deduct_review" and batch.status in ("deductibility", "deduction_accepted", "return_ready"):
        return reverse("batch_deduct", args=[batch.pk])
    if key == "signoff" and batch.status in ("deduction_accepted", "return_ready"):
        return reverse("batch_signoff", args=[batch.pk])
    if key == "return" and batch.status == "return_ready":
        return reverse("batch_return", args=[batch.pk])
    return ""
