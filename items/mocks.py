import random

from .vat import RATES

MOCK_INVOICES = [
    {
        "supplier": "Harbour Consulting Ltd",
        "invoice_number": "HC-1042",
        "invoice_date": "2026-03-12",
        "currency": "EUR",
        "lines": [
            {
                "description": "Management consulting",
                "net_amount": "1500.00",
                "vat_amount": "345.00",
                "gross_amount": "1845.00",
            },
            {
                "description": "Travel expenses",
                "net_amount": "80.00",
                "vat_amount": "18.40",
                "gross_amount": "98.40",
            },
        ],
    },
    {
        "supplier": "Green Fuel Oils",
        "invoice_number": "GF-338",
        "invoice_date": "2026-03-18",
        "currency": "EUR",
        "lines": [
            {
                "description": "Heating oil",
                "net_amount": "640.00",
                "vat_amount": "86.40",
                "gross_amount": "726.40",
            }
        ],
    },
    {
        "supplier": "City Newsagents",
        "invoice_number": "CN-90",
        "invoice_date": "2026-03-20",
        "currency": "EUR",
        "lines": [
            {
                "description": "Newspapers",
                "net_amount": "45.00",
                "vat_amount": "0.00",
                "gross_amount": "45.00",
            },
            {
                "description": "Stationery",
                "net_amount": "30.00",
                "vat_amount": "6.90",
                "gross_amount": "36.90",
            },
        ],
    },
    {
        "supplier": "Northside Repairs",
        "invoice_number": "NR-17",
        "invoice_date": "2026-03-22",
        "currency": "EUR",
        "lines": [
            {
                "description": "Building repairs",
                "net_amount": "900.00",
                "vat_amount": "121.50",
                "gross_amount": "1021.50",
            }
        ],
    },
]


def mock_invoice(index):
    return MOCK_INVOICES[index % len(MOCK_INVOICES)]


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
