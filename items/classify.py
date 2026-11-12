from django.conf import settings

from .httpapi import (
    ClientError,
    join_url,
    output_text,
    parse_json_content,
    post_json,
    require_api_key,
)
from .vat import RATES

CLASSIFY_PROMPT = """Classify Irish VAT for each invoice line and show the chain. Return only JSON:
{"lines":[{"line_id":1,"supply":"taxable","nature":"service","place":"ireland","who_accounts":"supplier","category":"standard","confidence":0.9,"reason":"","chain":[{"step":"Supply","finding":"","because":""},{"step":"Nature","finding":"","because":""},{"step":"Place of supply","finding":"","because":""},{"step":"Rate","finding":"","because":""},{"step":"Who accounts","finding":"","because":""}]}]}
category must be one of: standard, reduced, second_reduced, livestock, zero, exempt, reverse_charge, outside_scope.
supply is taxable, exempt, outside, or none.
nature is good or service.
place is ireland, eu, or non_eu.
who_accounts is supplier, customer, or none.
confidence is a number from 0 to 1.
The chain must show whether there is a supply, whether it is a good or a service, the place of supply, the rate, and who accounts for the VAT.
Use the amounts and descriptions provided. Do not invent lines.
"""


def classify_lines(lines):
    payload = {
        "lines": [
            {
                "line_id": line.id,
                "supplier": line.supplier,
                "invoice_number": line.invoice_number,
                "invoice_date": line.invoice_date,
                "description": line.description,
                "net_amount": line.net_amount,
                "vat_amount": line.vat_amount,
                "gross_amount": line.gross_amount,
                "currency": line.currency,
            }
            for line in lines
        ]
    }
    base = (settings.VAT_CLASSIFICATION_BASE_URL or "").strip()
    endpoint = (settings.VAT_CLASSIFICATION_ENDPOINT or "").strip()
    api_key = require_api_key()
    if base and endpoint:
        response = post_json(join_url(base, endpoint), payload, api_key)
    else:
        response = grok_classify(payload, api_key)
    return normalize_classification(response)


def grok_classify(payload, api_key):
    model = (settings.GROK_MODEL or "grok-4.7").strip()
    response = post_json(
        "https://api.x.ai/v1/responses",
        {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": CLASSIFY_PROMPT + "\n" + _json(payload),
                        }
                    ],
                }
            ],
        },
        api_key,
    )
    return parse_json_content(output_text(response))


def normalize_classification(payload):
    if not isinstance(payload, dict):
        raise ClientError("VAT classification returned an unexpected response.")
    rows = payload.get("lines")
    if not isinstance(rows, list):
        raise ClientError("VAT classification returned no lines.")
    cleaned = []
    for row in rows:
        if not isinstance(row, dict) or "line_id" not in row:
            continue
        category = str(row.get("category") or "")
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        cleaned.append(
            {
                "line_id": int(row["line_id"]),
                "category": category if category in RATES else "",
                "confidence": confidence,
                "reason": str(row.get("reason") or ""),
                "error": "" if category in RATES else "Unknown VAT category.",
                "supply": str(row.get("supply") or ""),
                "nature": str(row.get("nature") or ""),
                "place": str(row.get("place") or ""),
                "who_accounts": str(row.get("who_accounts") or ""),
                "chain": _chain(row.get("chain")),
            }
        )
    if not cleaned:
        raise ClientError("VAT classification returned no lines.")
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
