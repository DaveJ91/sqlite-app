from datetime import datetime, time

from django.utils import timezone

from .models import AuditEvent, Batch, Invoice, InvoiceLine, VatRun
from .training import amounts, classification_for, deductibility_for, register_descriptions
from .vat3 import payment_boxes, period_date_from_label, shift_month

# Ten suppliers, five lines each. The third item on each row is the training key.
_CATALOG = [
    (
        "Harbour Consulting Ltd",
        "HC-1042",
        "2026-03-02",
        [
            ("Management consulting", "1500.00", "consulting"),
            ("Workshop facilitation", "800.00", "consulting"),
            ("Hotel accommodation", "220.00", "hotel"),
            ("Research briefing", "450.00", "consulting"),
            ("Board pack design", "220.00", "consulting"),
        ],
    ),
    (
        "Green Fuel Oils",
        "GF-338",
        "2026-03-04",
        [
            ("Heating oil", "640.00", "heating"),
            ("Diesel", "510.00", "diesel"),
            ("Petrol", "300.00", "petrol"),
            ("Tank rental", "75.00", "overhead"),
            ("Emergency call-out", "140.00", "repairs"),
        ],
    ),
    (
        "City Newsagents",
        "CN-90",
        "2026-03-05",
        [
            ("Newspapers", "45.00", "newspaper"),
            ("Trade magazines", "62.00", "newspaper"),
            ("Stationery", "30.00", "stationery"),
            ("Printer paper", "48.00", "stationery"),
            ("Postage stamps", "25.00", "stamps"),
        ],
    ),
    (
        "Northside Repairs",
        "NR-17",
        "2026-03-06",
        [
            ("Building repairs", "900.00", "repairs"),
            ("Plumbing labour", "320.00", "repairs"),
            ("Replacement fittings", "150.00", "stationery"),
            ("Emergency leak", "210.00", "repairs"),
            ("Site visit", "80.00", "overhead"),
        ],
    ),
    (
        "Dublin Office Supply",
        "DOS-551",
        "2026-03-09",
        [
            ("Desk chairs", "740.00", "stationery"),
            ("Monitor arms", "260.00", "stationery"),
            ("Notebooks", "54.00", "stationery"),
            ("Toner cartridges", "128.00", "stationery"),
            ("Company car hire", "480.00", "car"),
        ],
    ),
    (
        "Liffey Catering",
        "LC-208",
        "2026-03-11",
        [
            ("Working lunch", "186.00", "food"),
            ("Conference coffee", "92.00", "food"),
            ("Evening reception", "640.00", "entertainment"),
            ("Staff canteen", "410.00", "canteen"),
            ("Disposable service", "36.00", "stationery"),
        ],
    ),
    (
        "Canal Software",
        "CS-77",
        "2026-03-13",
        [
            ("Software subscription", "1200.00", "software"),
            ("Implementation support", "960.00", "software"),
            ("Training seats", "480.00", "software"),
            ("Data migration", "700.00", "software"),
            ("EU software licence", "900.00", "eu_software"),
        ],
    ),
    (
        "Phoenix Print",
        "PP-412",
        "2026-03-16",
        [
            ("Letterheads", "180.00", "stationery"),
            ("Business cards", "70.00", "stationery"),
            ("Brochures", "340.00", "stationery"),
            ("Banner", "125.00", "stationery"),
            ("Proofing", "40.00", "overhead"),
        ],
    ),
    (
        "Ennis Mart",
        "EM-12",
        "2026-03-18",
        [
            ("Store cattle", "880.00", "livestock"),
            ("Calves", "460.00", "livestock"),
            ("Haulage", "90.00", "overhead"),
            ("Mart commission", "74.00", "overhead"),
            ("Animal feed", "110.00", "feed"),
        ],
    ),
    (
        "Merrion Legal",
        "ML-19",
        "2026-03-20",
        [
            ("Contract review", "1100.00", "legal"),
            ("Companies office filing", "180.00", "disbursement"),
            ("Meeting attendance", "400.00", "legal"),
            ("Letter of advice", "650.00", "legal"),
            ("Client entertainment", "55.00", "entertainment"),
        ],
    ),
]

register_descriptions(
    {description: treatment for _supplier, _number, _date, rows in _CATALOG for description, _net, treatment in rows}
)


def _line(description, net, treatment):
    priced = amounts(net, treatment)
    priced["description"] = description
    return priced


def catalog():
    invoices = []
    for supplier, number, date, rows in _CATALOG:
        invoices.append(
            {
                "supplier": supplier,
                "invoice_number": number,
                "invoice_date": date,
                "currency": "EUR",
                "filename": f"{number}.pdf",
                "lines": [_line(description, net, treatment) for description, net, treatment in rows],
            }
        )
    return invoices


def mock_invoice(index):
    invoices = catalog()
    return invoices[index % len(invoices)]


def mock_line_count():
    return sum(len(invoice["lines"]) for invoice in catalog())


def seed_mock_batch(period_label=""):
    batch = Batch.objects.create(
        period_label=period_label or "Mock March 2026",
        status=Batch.EXTRACTED,
    )
    total = 0
    for invoice_data in catalog():
        invoice = Invoice.objects.create(
            batch=batch,
            original_name=invoice_data["filename"],
            read_source="mock",
            raw_response={"source": "mock"},
        )
        for row in invoice_data["lines"]:
            InvoiceLine.objects.create(
                invoice=invoice,
                supplier=invoice_data["supplier"],
                extracted_supplier=invoice_data["supplier"],
                invoice_number=invoice_data["invoice_number"],
                extracted_invoice_number=invoice_data["invoice_number"],
                invoice_date=invoice_data["invoice_date"],
                extracted_invoice_date=invoice_data["invoice_date"],
                currency=invoice_data["currency"],
                extracted_currency=invoice_data["currency"],
                description=row["description"],
                extracted_description=row["description"],
                net_amount=row["net_amount"],
                extracted_net_amount=row["net_amount"],
                vat_amount=row["vat_amount"],
                extracted_vat_amount=row["vat_amount"],
                gross_amount=row["gross_amount"],
                extracted_gross_amount=row["gross_amount"],
            )
            total += 1
    AuditEvent.objects.create(
        batch=batch,
        kind="extracted",
        payload={"ok": True, "source": "mock", "line_count": total, "files": 0},
    )
    VatRun.objects.create(
        batch=batch,
        kind=VatRun.EXTRACT,
        period_label=batch.period_label,
        period_date=period_date_from_label(batch.period_label),
        mock=True,
        payload={"ok": True, "source": "mock", "line_count": total, "files": 0},
    )
    seed_return_history()
    return batch


# Twelve ordinary payment months. A purchase-heavy return should look odd beside them.
_HISTORY = (
    ("4600.00", "1900.00"),
    ("4700.00", "2000.00"),
    ("4550.00", "1850.00"),
    ("4900.00", "2100.00"),
    ("4800.00", "2050.00"),
    ("5000.00", "2200.00"),
    ("4750.00", "1980.00"),
    ("4650.00", "1920.00"),
    ("4850.00", "2070.00"),
    ("5100.00", "2300.00"),
    ("4700.00", "2000.00"),
    ("4950.00", "2150.00"),
)


def seed_return_history():
    if VatRun.objects.filter(kind=VatRun.RETURN, mock=True, batch__isnull=True).count() >= 12:
        return
    VatRun.objects.filter(kind=VatRun.RETURN, mock=True, batch__isnull=True).delete()
    anchor = timezone.localdate().replace(day=1)
    for index, (t1, t2) in enumerate(_HISTORY):
        period = shift_month(anchor, -(12 - index))
        run = VatRun.objects.create(
            kind=VatRun.RETURN,
            period_label=period.strftime("%B %Y"),
            period_date=period,
            mock=True,
            payload={"boxes": payment_boxes(t1, t2), "seed": True},
        )
        when = timezone.make_aware(datetime.combine(period, time(9, 0)))
        VatRun.objects.filter(pk=run.pk).update(created=when)


def backfill_mock_history():
    if Invoice.objects.filter(read_source="mock").exists():
        seed_return_history()
    missing = (
        Batch.objects.filter(invoices__read_source="mock")
        .exclude(runs__kind=VatRun.EXTRACT)
        .distinct()
    )
    for batch in missing:
        VatRun.objects.create(
            batch=batch,
            kind=VatRun.EXTRACT,
            period_label=batch.period_label,
            period_date=period_date_from_label(batch.period_label),
            mock=True,
            payload={
                "source": "mock",
                "line_count": InvoiceLine.objects.filter(invoice__batch=batch).count(),
                "files": 0,
            },
        )


def mock_classification(lines):
    rows = []
    for line in lines:
        trained = classification_for(line.description)
        trained["line_id"] = line.id
        rows.append(trained)
    return rows


def mock_deductibility(lines):
    rows = []
    for line in lines:
        trained = deductibility_for(line.description)
        trained["line_id"] = line.id
        rows.append(trained)
    return rows
