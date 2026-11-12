from django.conf import settings

from .httpapi import (
    ClientError,
    join_url,
    output_text,
    parse_json_content,
    post_file,
    post_json,
    require_api_key,
)

EXTRACT_PROMPT = """Read this invoice and return only JSON with this shape:
{"supplier":"","invoice_number":"","invoice_date":"","currency":"","lines":[{"description":"","net_amount":"","vat_amount":"","gross_amount":""}]}
Use figures and descriptions printed on the invoice. Do not invent amounts. Leave a field as an empty string when it is not printed.
"""


def understand_invoice(path, filename):
    base = (settings.CONTENT_UNDERSTANDING_BASE_URL or "").strip()
    endpoint = (settings.CONTENT_UNDERSTANDING_ENDPOINT or "").strip()
    if base and endpoint:
        payload = post_file(
            join_url(base, endpoint),
            path,
            filename,
            require_api_key(),
        )
        return normalize_invoice(payload), "content_understanding"
    return grok_read_invoice(path, filename), "grok"


def grok_read_invoice(path, filename):
    api_key = require_api_key()
    uploaded = post_file(
        "https://api.x.ai/v1/files",
        path,
        filename,
        api_key,
        extra_fields={"purpose": "assistants"},
    )
    file_id = uploaded.get("id") or uploaded.get("file_id")
    if not file_id:
        raise ClientError("Grok did not accept the invoice file.")
    model = (settings.GROK_MODEL or "grok-4.7").strip()
    response = post_json(
        "https://api.x.ai/v1/responses",
        {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": EXTRACT_PROMPT},
                        {"type": "input_file", "file_id": file_id},
                    ],
                }
            ],
        },
        api_key,
    )
    return normalize_invoice(parse_json_content(output_text(response)))


def normalize_invoice(payload):
    if not isinstance(payload, dict):
        raise ClientError("Content understanding returned an unexpected response.")
    if "lines" not in payload and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    lines = payload.get("lines") or []
    if not isinstance(lines, list) or not lines:
        raise ClientError("No lines were read from this invoice.")
    cleaned_lines = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        cleaned_lines.append(
            {
                "description": str(line.get("description") or ""),
                "net_amount": str(line.get("net_amount") or ""),
                "vat_amount": str(line.get("vat_amount") or ""),
                "gross_amount": str(line.get("gross_amount") or ""),
            }
        )
    if not cleaned_lines:
        raise ClientError("No lines were read from this invoice.")
    return {
        "supplier": str(payload.get("supplier") or ""),
        "invoice_number": str(payload.get("invoice_number") or ""),
        "invoice_date": str(payload.get("invoice_date") or ""),
        "currency": str(payload.get("currency") or ""),
        "lines": cleaned_lines,
    }
