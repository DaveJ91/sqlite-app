"""Deterministic checks. These sit beside the model, and they survive an override."""

from decimal import Decimal

from .training import RATE_FRACTION
from .vat import parse_amount

MONEY_TOLERANCE = Decimal("0.02")
BLOCKED_OK = {"", "none"}


def line_flags(line, duplicate=False):
    flags = []
    if line.low_confidence:
        flags.append("Rate confidence is below the threshold.")
    if getattr(line, "deductibility_low", False):
        flags.append("Deductibility confidence is below the threshold.")
    if not (line.supplier or "").strip():
        flags.append("Supplier is blank.")
    if not (line.invoice_number or "").strip():
        flags.append("Invoice number is blank.")
    if not (line.invoice_date or "").strip():
        flags.append("Invoice date is blank.")
    currency = (line.currency or "").strip().upper()
    if currency and currency not in {"EUR", "€"}:
        flags.append(f"Currency is {line.currency}, not euro.")
    if duplicate and (line.invoice_number or "").strip():
        flags.append(f"Invoice number {line.invoice_number} appears on more than one invoice.")
    flags.extend(_money_flags(line))
    flags.extend(_deduct_flags(line))
    return flags


def batch_flags(lines):
    numbers = {}
    for line in lines:
        number = (line.invoice_number or "").strip()
        if number:
            numbers.setdefault(number, set()).add(line.invoice_id)
    duplicates = {number for number, invoices in numbers.items() if len(invoices) > 1}
    found = []
    for line in lines:
        for text in line_flags(line, duplicate=(line.invoice_number or "").strip() in duplicates):
            found.append({"line_id": line.id, "description": line.description, "text": text})
    return found


def _money_flags(line):
    flags = []
    net = parse_amount(line.net_amount)
    vat = parse_amount(line.vat_amount)
    gross = parse_amount(line.gross_amount)
    if (line.gross_amount or "").strip() and abs(gross - net - vat) > MONEY_TOLERANCE:
        flags.append("Gross does not equal net plus VAT.")
    category = line.category or ""
    fraction = RATE_FRACTION.get(category)
    if fraction is not None and fraction > 0 and net > 0:
        expected = (net * fraction).quantize(Decimal("0.01"))
        if abs(vat - expected) > MONEY_TOLERANCE:
            flags.append(f"VAT {vat} is not {fraction * 100}% of the net.")
    if category in {"exempt", "zero", "outside_scope"} and vat > MONEY_TOLERANCE:
        flags.append("VAT is charged on a supply classed as exempt, zero-rated, or outside the scope.")
    if category == "reverse_charge" and vat > MONEY_TOLERANCE:
        flags.append("The supplier charged VAT on a reverse-charge line.")
    return flags


def _deduct_flags(line):
    code = getattr(line, "deductible", "") or ""
    if not code:
        return []
    blocked = getattr(line, "blocked_reason", "") or ""
    flags = []
    if code == "full" and blocked not in BLOCKED_OK:
        flags.append(f"Marked fully deductible while the blocked-input reason is {blocked.replace('_', ' ')}.")
    if code == "none" and blocked in BLOCKED_OK and parse_amount(line.vat_amount) > MONEY_TOLERANCE:
        flags.append("Marked non-deductible with no blocked-input reason.")
    return flags
