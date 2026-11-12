"""Irish VAT3 working paper for a batch of purchase invoices.

Revenue's VAT3 uses zeros, not the word nil. This is not a filing.
Purchase invoices have no output VAT on sales, so T1 is reverse-charge
notional VAT only. Deductible reverse-charge VAT is also in T2, and the
net of those services goes to ES2. E1, E2, ES1, and PA1 are not inferred.
"""

import calendar
from datetime import date
from decimal import Decimal

from .vat import format_amount, parse_amount

BOX_CODES = ("T1", "T2", "T3", "T4", "E1", "E2", "ES1", "ES2", "PA1")

BOX_LABELS = {
    "T1": "VAT on sales",
    "T2": "VAT on purchases",
    "T3": "VAT payable",
    "T4": "VAT repayable",
    "E1": "Goods to other EU countries",
    "E2": "Goods from other EU countries",
    "ES1": "Services to other EU countries",
    "ES2": "Services from other EU countries",
    "PA1": "Postponed accounting imports",
}

INPUT_VAT = {"standard", "reduced", "second_reduced", "livestock"}
EU_WATCH = ("E1", "E2", "ES1", "ES2", "PA1")
STANDARD_RATE = Decimal("0.23")
DEVIATION = Decimal("0.25")


def vat3_boxes(lines):
    t1 = Decimal("0.00")
    t2 = Decimal("0.00")
    es2 = Decimal("0.00")
    for line in lines:
        category = getattr(line, "category", "")
        if category in INPUT_VAT:
            t2 += parse_amount(line.vat_amount)
        elif category == "reverse_charge":
            notional = parse_amount(line.vat_amount)
            if notional == 0:
                notional = (parse_amount(line.net_amount) * STANDARD_RATE).quantize(Decimal("0.01"))
            t1 += notional
            t2 += notional
            es2 += parse_amount(line.net_amount)
    boxes = _finish(t1, t2)
    boxes["ES2"] = format_amount(es2)
    return boxes


def payment_boxes(t1, t2):
    return _finish(Decimal(t1), Decimal(t2))


def box_rows(boxes):
    outcome = boxes.get("outcome")
    rows = []
    for code in BOX_CODES:
        hot = (code == "T3" and outcome == "payment") or (code == "T4" and outcome == "refund")
        rows.append(
            {
                "code": code,
                "label": BOX_LABELS[code],
                "amount": boxes.get(code, "0.00"),
                "hot": hot,
            }
        )
    return rows


def anomalies(current, priors):
    """priors are newest first. The 25% check waits until there are three."""
    flags = []
    if len(priors) >= 3:
        metrics = (
            ("T1", "T1", False),
            ("T2", "T2", False),
            ("net", "Net VAT", True),
        )
        for key, label, is_net in metrics:
            current_value = _net(current) if is_net else _dec(current, key)
            values = [(_net(prior) if is_net else _dec(prior, key)) for prior in priors]
            mean = sum(values, Decimal("0")) / Decimal(len(values))
            if abs(mean) == 0:
                if current_value != 0:
                    flags.append(
                        {
                            "code": key,
                            "text": (
                                f"{label} was zero across the earlier returns "
                                f"and is {format_amount(current_value)} now."
                            ),
                        }
                    )
            elif abs(current_value - mean) / abs(mean) > DEVIATION:
                flags.append(
                    {
                        "code": key,
                        "text": (
                            f"{label} is {format_amount(current_value)}, more than 25% "
                            f"from the 12-month average of {format_amount(mean)}."
                        ),
                    }
                )
        for code in EU_WATCH:
            current_value = _dec(current, code)
            if current_value != 0 and all(_dec(prior, code) == 0 for prior in priors):
                flags.append(
                    {
                        "code": code,
                        "text": (
                            f"{code} was zero on the earlier returns "
                            f"and is {format_amount(current_value)} now."
                        ),
                    }
                )
    if priors:
        previous = priors[0].get("outcome")
        outcome = current.get("outcome")
        if previous in ("payment", "refund") and outcome in ("payment", "refund") and previous != outcome:
            flags.append(
                {
                    "code": "outcome",
                    "text": f"This return is a {outcome}. The previous return was a {previous}.",
                }
            )
    return flags


def chart_months(points, today=None):
    """points are (period_date, boxes), chronological. Twelve columns, oldest first."""
    today = today or date.today()
    start = shift_month(today.replace(day=1), -11)
    by_month = {}
    for period, boxes in points:
        if period is None:
            continue
        by_month[period.replace(day=1)] = boxes or {}
    months = []
    amounts = []
    for index in range(12):
        key = shift_month(start, index)
        boxes = by_month.get(key, {})
        t1 = _dec(boxes, "T1")
        t2 = _dec(boxes, "T2")
        amounts.append((t1, t2))
        months.append(
            {
                "key": key.isoformat(),
                "label": key.strftime("%b"),
                "year": key.strftime("%Y"),
                "t1": format_amount(t1),
                "t2": format_amount(t2),
                "filled": bool(boxes),
            }
        )
    peak = max([max(pair) for pair in amounts] + [Decimal("1")])
    for month, (t1, t2) in zip(months, amounts):
        month["t1_pct"] = _pct(t1, peak)
        month["t2_pct"] = _pct(t2, peak)
    return months


def period_date_from_label(label, fallback=None):
    months = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}
    months.update({name.lower(): index for index, name in enumerate(calendar.month_abbr) if name})
    found_month = None
    found_year = None
    for part in str(label or "").replace(",", " ").split():
        if part.lower() in months:
            found_month = months[part.lower()]
        elif part.isdigit() and len(part) == 4:
            found_year = int(part)
    if found_month and found_year:
        return date(found_year, found_month, 1)
    return fallback or date.today().replace(day=1)


def shift_month(day, offset):
    month_index = day.month - 1 + offset
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _finish(t1, t2):
    if t1 > t2:
        outcome = "payment"
        t3, t4, due = t1 - t2, Decimal("0"), t1 - t2
    elif t2 > t1:
        outcome = "refund"
        t3, t4, due = Decimal("0"), t2 - t1, t2 - t1
    else:
        outcome = "clear"
        t3 = t4 = due = Decimal("0")
    boxes = {code: "0.00" for code in BOX_CODES}
    boxes.update(
        {
            "T1": format_amount(t1),
            "T2": format_amount(t2),
            "T3": format_amount(t3),
            "T4": format_amount(t4),
            "outcome": outcome,
            "due": format_amount(due),
        }
    )
    return boxes


def _net(boxes):
    return _dec(boxes, "T1") - _dec(boxes, "T2")


def _dec(boxes, key):
    if not boxes:
        return Decimal("0.00")
    return parse_amount(boxes.get(key, "0"))


def _pct(value, peak):
    if value <= 0 or peak <= 0:
        return 0
    return max(4, int((value / peak) * 100))
