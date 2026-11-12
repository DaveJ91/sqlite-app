import random
from decimal import Decimal

from .models import AuditEvent, Batch, Invoice, InvoiceLine
from .vat import RATES

# Ten suppliers, five lines each. Fifty rows, no files required.
_CATALOG = [
    (
        "Harbour Consulting Ltd",
        "HC-1042",
        "2026-03-02",
        [
            ("Management consulting", "1500.00"),
            ("Workshop facilitation", "800.00"),
            ("Travel expenses", "80.00"),
            ("Research briefing", "450.00"),
            ("Board pack design", "220.00"),
        ],
    ),
    (
        "Green Fuel Oils",
        "GF-338",
        "2026-03-04",
        [
            ("Heating oil", "640.00"),
            ("Diesel", "510.00"),
            ("Tank rental", "75.00"),
            ("Emergency call-out", "140.00"),
            ("Fuel surcharge", "36.00"),
        ],
    ),
    (
        "City Newsagents",
        "CN-90",
        "2026-03-05",
        [
            ("Newspapers", "45.00"),
            ("Trade magazines", "62.00"),
            ("Stationery", "30.00"),
            ("Printer paper", "48.00"),
            ("Postage stamps", "25.00"),
        ],
    ),
    (
        "Northside Repairs",
        "NR-17",
        "2026-03-06",
        [
            ("Building repairs", "900.00"),
            ("Plumbing labour", "320.00"),
            ("Replacement fittings", "150.00"),
            ("Emergency leak", "210.00"),
            ("Site visit", "80.00"),
        ],
    ),
    (
        "Dublin Office Supply",
        "DOS-551",
        "2026-03-09",
        [
            ("Desk chairs", "740.00"),
            ("Monitor arms", "260.00"),
            ("Notebooks", "54.00"),
            ("Toner cartridges", "128.00"),
            ("Whiteboard", "95.00"),
        ],
    ),
    (
        "Liffey Catering",
        "LC-208",
        "2026-03-11",
        [
            ("Working lunch", "186.00"),
            ("Conference coffee", "92.00"),
            ("Evening reception", "640.00"),
            ("Staff canteen", "410.00"),
            ("Disposable service", "36.00"),
        ],
    ),
    (
        "Canal Software",
        "CS-77",
        "2026-03-13",
        [
            ("Software subscription", "1200.00"),
            ("Implementation support", "960.00"),
            ("Training seats", "480.00"),
            ("Data migration", "700.00"),
            ("Priority support", "150.00"),
        ],
    ),
    (
        "Phoenix Print",
        "PP-412",
        "2026-03-16",
        [
            ("Letterheads", "180.00"),
            ("Business cards", "70.00"),
            ("Brochures", "340.00"),
            ("Banner", "125.00"),
            ("Proofing", "40.00"),
        ],
    ),
    (
        "Wicklow Timber",
        "WT-63",
        "2026-03-18",
        [
            ("Timber supplies", "880.00"),
            ("Sheet materials", "460.00"),
            ("Fixings", "74.00"),
            ("Delivery", "90.00"),
            ("Cutting service", "110.00"),
        ],
    ),
    (
        "Merrion Legal",
        "ML-19",
        "2026-03-20",
        [
            ("Contract review", "1100.00"),
            ("Companies office filing", "180.00"),
            ("Meeting attendance", "400.00"),
            ("Letter of advice", "650.00"),
            ("Disbursements", "55.00"),
        ],
    ),
]


def _line(description, net):
    net_amount = Decimal(net)
    vat_amount = (net_amount * Decimal("0.23")).quantize(Decimal("0.01"))
    gross_amount = net_amount + vat_amount
    return {
        "description": description,
        "net_amount": f"{net_amount:.2f}",
        "vat_amount": f"{vat_amount:.2f}",
        "gross_amount": f"{gross_amount:.2f}",
    }


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
                "lines": [_line(description, net) for description, net in rows],
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
    return batch


def mock_classification(lines):
    categories = list(RATES)
    rows = []
    for line in lines:
        category = random.choice(categories)
        confidence = round(random.uniform(0.35, 0.99), 2)
        rows.append(
            {
                "line_id": line.id,
                "category": category,
                "confidence": confidence,
                "reason": "Mock classification.",
                "error": "",
            }
        )
    return rows
