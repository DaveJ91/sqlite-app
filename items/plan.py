from django.urls import reverse

from .models import InvoiceLine

STEPS = ("load", "review", "classify", "vat", "return")

TITLES = {
    "load": "Load invoices",
    "review": "Review extraction",
    "classify": "Classify VAT",
    "vat": "Review classifications",
    "return": "Prepare the return",
}


def workspace_plan(batch):
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
        "classify": "Live API, or a random mock",
        "vat": _vat_detail(batch, counts),
        "return": "Totals by rate, with every source line",
    }
    steps = []
    for key in STEPS:
        if current is None or (key in done and key != current):
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
    if current is None:
        kicker = "Complete"
        current_title = "VAT return"
    else:
        kicker = f"Step {STEPS.index(current) + 1} of {len(STEPS)}"
        current_title = TITLES[current]
    return {"steps": steps, "kicker": kicker, "current_title": current_title}


def _progress(status):
    if status == "return_ready":
        return set(STEPS), None
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
    if batch is None or batch.status not in ("classified", "return_ready"):
        return "Low-confidence rows get flagged"
    if counts["low"]:
        return f"{counts['low']} low-confidence lines"
    return "Every line is above the threshold"


def _href(batch, key, done, current):
    if batch is None or (key not in done and key != current):
        return ""
    if key == "load":
        return reverse("batch_list")
    if key in ("review", "classify"):
        return reverse("batch_detail", args=[batch.pk])
    if key == "vat" and batch.status in ("classified", "return_ready"):
        return reverse("batch_vat", args=[batch.pk])
    if key == "return" and batch.status == "return_ready":
        return reverse("batch_return", args=[batch.pk])
    return ""
