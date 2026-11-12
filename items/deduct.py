from django.conf import settings

from .httpapi import (
    ClientError,
    join_url,
    output_text,
    parse_json_content,
    post_json,
    require_api_key,
)

DEDUCT_PROMPT = """Assess Irish VAT deductibility for each purchase line. Return only JSON:
{"lines":[{"line_id":1,"deductible":"full","deductible_percent":"100","blocked_reason":"none","confidence":0.9,"reason":"","chain":[{"step":"Taxable use","finding":"","because":""},{"step":"Blocked input","finding":"","because":""},{"step":"Apportionment","finding":"","because":""},{"step":"Recoverable VAT","finding":"","because":""}]}]}
deductible is full, partial, or none.
deductible_percent is 100 for full, 0 for none, or the recoverable fraction for partial. A qualifying passenger car is 20 when business use is at least 60%.
blocked_reason is one of: none, food, accommodation, entertainment, personal, petrol, passenger_car, exempt_use, non_business, no_vat.
Use VATCA 2010 s.59 for the general deduction, s.60 for blocked inputs (food, drink, accommodation, entertainment, other personal services, petrol, passenger motor vehicles), s.59(2)(d) and s.62 for the 20% qualifying-car fraction, and s.61 for dual use.
A staff canteen can be deductible. Petrol is blocked unless it is stock. Diesel is not petrol. Reverse-charge notional VAT is deductible when the service is for taxable supplies.
This is a working paper, not a Revenue ruling. Do not invent lines.
"""

ALLOWED_DEDUCTIBLE = {"full", "partial", "none"}
ALLOWED_BLOCKED = {
    "none",
    "food",
    "accommodation",
    "entertainment",
    "personal",
    "petrol",
    "passenger_car",
    "exempt_use",
    "non_business",
    "no_vat",
}


def deduct_lines(lines):
    payload = {
        "lines": [
            {
                "line_id": line.id,
                "supplier": line.supplier,
                "description": line.description,
                "net_amount": line.net_amount,
                "vat_amount": line.vat_amount,
                "category": line.category,
                "rate": line.rate,
                "supply": line.supply,
                "nature": line.nature,
                "place": line.place_of_supply,
                "who_accounts": line.who_accounts,
                "classification_reason": line.reason,
            }
            for line in lines
        ]
    }
    base = (settings.VAT_CLASSIFICATION_BASE_URL or "").strip()
    endpoint = (getattr(settings, "VAT_DEDUCTIBILITY_ENDPOINT", "") or "").strip()
    api_key = require_api_key()
    if base and endpoint:
        response = post_json(join_url(base, endpoint), payload, api_key)
    else:
        response = grok_deduct(payload, api_key)
    return normalize_deductibility(response)


def grok_deduct(payload, api_key):
    model = (settings.GROK_MODEL or "grok-4.7").strip()
    response = post_json(
        "https://api.x.ai/v1/responses",
        {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": DEDUCT_PROMPT + "\n" + _json(payload)}],
                }
            ],
        },
        api_key,
    )
    return parse_json_content(output_text(response))


def normalize_deductibility(payload):
    if not isinstance(payload, dict):
        raise ClientError("Deductibility returned an unexpected response.")
    rows = payload.get("lines")
    if not isinstance(rows, list):
        raise ClientError("Deductibility returned no lines.")
    cleaned = []
    for row in rows:
        if not isinstance(row, dict) or "line_id" not in row:
            continue
        deductible = str(row.get("deductible") or "")
        blocked = str(row.get("blocked_reason") or "")
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        error = ""
        if deductible not in ALLOWED_DEDUCTIBLE:
            error = "Unknown deductibility."
            deductible = ""
        if blocked not in ALLOWED_BLOCKED:
            blocked = "none"
        percent = str(row.get("deductible_percent") or "").strip()
        if deductible == "full":
            percent = percent or "100"
        elif deductible == "none":
            percent = "0"
        cleaned.append(
            {
                "line_id": int(row["line_id"]),
                "deductible": deductible,
                "deductible_percent": percent or "0",
                "blocked_reason": blocked,
                "confidence": confidence,
                "reason": str(row.get("reason") or ""),
                "chain": _chain(row.get("chain")),
                "error": error,
            }
        )
    if not cleaned:
        raise ClientError("Deductibility returned no lines.")
    return cleaned


def _chain(value):
    if not isinstance(value, list):
        return []
    steps = []
    for step in value:
        if not isinstance(step, dict):
            continue
        steps.append(
            {
                "step": str(step.get("step") or ""),
                "finding": str(step.get("finding") or ""),
                "because": str(step.get("because") or ""),
            }
        )
    return steps


def _json(payload):
    import json

    return json.dumps(payload)
