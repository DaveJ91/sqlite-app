from decimal import Decimal, InvalidOperation

RATES = {
    "standard": "23%",
    "reduced": "13.5%",
    "second_reduced": "9%",
    "livestock": "4.8%",
    "zero": "0%",
    "exempt": "Exempt",
    "reverse_charge": "Reverse charge",
    "outside_scope": "Outside scope",
}

LABELS = {
    "standard": "Standard 23%",
    "reduced": "Reduced 13.5%",
    "second_reduced": "Second reduced 9%",
    "livestock": "Livestock 4.8%",
    "zero": "Zero 0%",
    "exempt": "Exempt",
    "reverse_charge": "Reverse charge",
    "outside_scope": "Outside scope",
}


def parse_amount(value):
    text = str(value or "").strip()
    for token in (",", "€", "EUR", "eur"):
        text = text.replace(token, "")
    text = text.strip()
    if not text:
        return Decimal("0.00")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0.00")


def format_amount(value):
    return f"{value.quantize(Decimal('0.01'))}"
