import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class ClientError(Exception):
    pass


def require_api_key():
    from django.conf import settings

    key = (settings.XAI_API_KEY or "").strip()
    if not key:
        raise ClientError("Set XAI_API_KEY in the .env file.")
    return key


def post_json(url, payload, api_key):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    return _read_json(request)


def post_file(url, path, filename, api_key, field_name="file", extra_fields=None):
    boundary = "----InvoiceBoundary" + uuid.uuid4().hex
    safe_name = Path(filename).name.replace('"', "")
    content_type = mimetypes.guess_type(safe_name)[0] or "application/pdf"
    chunks = []
    for name, value in (extra_fields or {}).items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    chunks.append(Path(path).read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=b"".join(chunks),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    return _read_json(request)


def join_url(base, endpoint):
    return base.rstrip("/") + "/" + endpoint.lstrip("/")


def output_text(payload):
    if isinstance(payload, str):
        return payload
    chunks = []
    if isinstance(payload.get("output_text"), str):
        chunks.append(payload["output_text"])
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            chunks.append(item["text"])
        content = item.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
    return "\n".join(chunks).strip()


def parse_json_content(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        data = json.loads(cleaned.strip())
    except json.JSONDecodeError as exc:
        raise ClientError("The model did not return JSON.") from exc
    if not isinstance(data, dict):
        raise ClientError("The model did not return a JSON object.")
    return data


def _read_json(request):
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise ClientError(f"Request failed ({exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise ClientError("Could not reach the endpoint.") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClientError("The endpoint did not return JSON.") from exc
